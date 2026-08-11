#!/usr/bin/env node

import { spawn } from "node:child_process";
import { randomBytes, timingSafeEqual } from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";

const DEFAULT_CODEX_BIN = "/Applications/ChatGPT.app/Contents/Resources/codex";
const REQUEST_FIELDS = ["images", "kind", "model", "prompt", "schema", "system"];
const IMAGE_EXTENSIONS = new Set([".jpeg", ".jpg", ".png", ".webp"]);
const MAX_REQUEST_BYTES = 2 * 1024 * 1024;
const MAX_SYSTEM_BYTES = 128 * 1024;
const MAX_PROMPT_BYTES = 1024 * 1024;
const MAX_SCHEMA_BYTES = 256 * 1024;
const MAX_IMAGES = 40;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_TOTAL_IMAGE_BYTES = 32 * 1024 * 1024;
const MAX_OUTPUT_BYTES = 2 * 1024 * 1024;
const MAX_CONCURRENT_REQUESTS = 2;
const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;

class SafeFailure extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function parseArguments(argv) {
  const values = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index];
    const value = argv[index + 1];
    if (!name?.startsWith("--") || value === undefined || values.has(name)) {
      throw new Error("invalid bridge arguments");
    }
    values.set(name, value);
  }
  const allowed = new Set([
    "--eval-root",
    "--host",
    "--port",
    "--ready-file",
    "--timeout-ms",
    "--token-file",
  ]);
  if ([...values.keys()].some((key) => !allowed.has(key))) {
    throw new Error("invalid bridge arguments");
  }
  for (const required of ["--eval-root", "--ready-file", "--token-file"]) {
    if (!values.get(required)) throw new Error("missing bridge argument");
  }
  const host = values.get("--host") ?? "127.0.0.1";
  if (!["127.0.0.1", "0.0.0.0", "::1"].includes(host)) {
    throw new Error("invalid bridge host");
  }
  const port = Number(values.get("--port") ?? "0");
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    throw new Error("invalid bridge port");
  }
  const timeoutMs = Number(values.get("--timeout-ms") ?? DEFAULT_TIMEOUT_MS);
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 10 * 60 * 1000) {
    throw new Error("invalid bridge timeout");
  }
  return {
    evalRoot: values.get("--eval-root"),
    host,
    port,
    readyFile: values.get("--ready-file"),
    timeoutMs,
    tokenFile: values.get("--token-file"),
  };
}

function safeJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Cache-Control": "no-store",
    "Content-Length": Buffer.byteLength(payload),
    "Content-Type": "application/json; charset=utf-8",
    "X-Content-Type-Options": "nosniff",
  });
  res.end(payload);
}

function safeError(res, failure) {
  safeJson(res, failure.status, {
    error: { code: failure.code, message: failure.message },
  });
}

function byteLength(value) {
  return Buffer.byteLength(value, "utf8");
}

async function readRequestBody(req) {
  const declaredLength = Number(req.headers["content-length"] ?? "0");
  if (Number.isFinite(declaredLength) && declaredLength > MAX_REQUEST_BYTES) {
    throw new SafeFailure(413, "request_too_large", "The request is too large");
  }
  return await new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    let tooLarge = false;
    req.on("data", (chunk) => {
      total += chunk.length;
      if (total > MAX_REQUEST_BYTES) {
        tooLarge = true;
        chunks.length = 0;
      } else if (!tooLarge) {
        chunks.push(chunk);
      }
    });
    req.on("end", () => {
      if (tooLarge) {
        reject(new SafeFailure(413, "request_too_large", "The request is too large"));
      } else {
        resolve(Buffer.concat(chunks).toString("utf8"));
      }
    });
    req.on("error", () => {
      reject(new SafeFailure(400, "invalid_request", "The request is invalid"));
    });
  });
}

function validBearer(header, token) {
  if (typeof header !== "string" || !header.startsWith("Bearer ")) return false;
  const supplied = Buffer.from(header.slice(7), "utf8");
  const expected = Buffer.from(token, "utf8");
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

function validatePayload(payload) {
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    throw new SafeFailure(400, "invalid_request", "The request is invalid");
  }
  const fields = Object.keys(payload).sort();
  if (
    fields.length !== REQUEST_FIELDS.length
    || fields.some((field, index) => field !== REQUEST_FIELDS[index])
  ) {
    throw new SafeFailure(400, "invalid_request", "The request is invalid");
  }
  if (!["agent", "judge"].includes(payload.kind)) {
    throw new SafeFailure(422, "invalid_kind", "The request kind is not supported");
  }
  if (
    typeof payload.model !== "string"
    || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(payload.model)
  ) {
    throw new SafeFailure(422, "invalid_model", "The requested model is not valid");
  }
  if (typeof payload.system !== "string" || byteLength(payload.system) > MAX_SYSTEM_BYTES) {
    throw new SafeFailure(413, "system_too_large", "The system instructions are too large");
  }
  if (typeof payload.prompt !== "string" || byteLength(payload.prompt) > MAX_PROMPT_BYTES) {
    throw new SafeFailure(413, "prompt_too_large", "The prompt is too large");
  }
  if (
    payload.schema === null
    || typeof payload.schema !== "object"
    || Array.isArray(payload.schema)
  ) {
    throw new SafeFailure(422, "invalid_schema", "The output schema is not valid");
  }
  let renderedSchema;
  try {
    renderedSchema = JSON.stringify(payload.schema);
  } catch {
    throw new SafeFailure(422, "invalid_schema", "The output schema is not valid");
  }
  if (byteLength(renderedSchema) > MAX_SCHEMA_BYTES) {
    throw new SafeFailure(413, "schema_too_large", "The output schema is too large");
  }
  if (!Array.isArray(payload.images) || payload.images.length > MAX_IMAGES) {
    throw new SafeFailure(413, "images_too_large", "Too many images were provided");
  }
  if (payload.images.some((image) => typeof image !== "string" || !image || image.includes("\0"))) {
    throw new SafeFailure(422, "invalid_image", "An image path is not valid");
  }
  return { ...payload, renderedSchema };
}

async function resolveImages(evalRoot, requestedImages) {
  const images = [];
  let totalBytes = 0;
  for (const requested of requestedImages) {
    if (path.isAbsolute(requested)) {
      throw new SafeFailure(422, "invalid_image", "An image path is not valid");
    }
    let resolved;
    try {
      resolved = await fsp.realpath(path.resolve(evalRoot, requested));
    } catch {
      throw new SafeFailure(422, "invalid_image", "An image path is not valid");
    }
    if (resolved !== evalRoot && !resolved.startsWith(`${evalRoot}${path.sep}`)) {
      throw new SafeFailure(422, "invalid_image", "An image path is not valid");
    }
    if (!IMAGE_EXTENSIONS.has(path.extname(resolved).toLowerCase())) {
      throw new SafeFailure(422, "invalid_image", "An image type is not supported");
    }
    const details = await fsp.stat(resolved);
    if (!details.isFile() || details.size > MAX_IMAGE_BYTES) {
      throw new SafeFailure(413, "image_too_large", "An image is too large");
    }
    totalBytes += details.size;
    if (totalBytes > MAX_TOTAL_IMAGE_BYTES) {
      throw new SafeFailure(413, "images_too_large", "The images are too large");
    }
    images.push(resolved);
  }
  return images;
}

function childEnvironment() {
  const allowed = [
    "CODEX_HOME",
    "HOME",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TMPDIR",
  ];
  return Object.fromEntries(
    allowed.flatMap((name) => process.env[name] ? [[name, process.env[name]]] : []),
  );
}

function stopChild(child, signal = "SIGTERM") {
  if (child.exitCode !== null || child.signalCode !== null) return;
  try {
    if (child.pid && process.platform !== "win32") process.kill(-child.pid, signal);
    else child.kill(signal);
  } catch {
    // The child may have exited between the state check and the signal.
  }
}

async function runCodex({ codexBin, evalRoot, images, payload, timeoutMs, activeChildren }) {
  const requestDir = await fsp.mkdtemp(path.join(os.tmpdir(), "sessionbuddy-codex-request-"));
  await fsp.chmod(requestDir, 0o700);
  const schemaPath = path.join(requestDir, "output-schema.json");
  const outputPath = path.join(requestDir, "output.json");
  await fsp.writeFile(schemaPath, `${payload.renderedSchema}\n`, { mode: 0o600 });
  const args = [
    "exec",
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--skip-git-repo-check",
    "--sandbox",
    "read-only",
    "--output-schema",
    schemaPath,
    "-o",
    outputPath,
    "-m",
    payload.model,
    ...images.flatMap((image) => ["-i", image]),
    "-",
  ];
  const envelope = JSON.stringify({
    protocol: "sessionbuddy-eval-v1",
    kind: payload.kind,
    system: payload.system,
    prompt: payload.prompt,
  });
  let child;
  let timeout;
  try {
    child = spawn(codexBin, args, {
      cwd: requestDir,
      detached: process.platform !== "win32",
      env: childEnvironment(),
      stdio: ["pipe", "ignore", "ignore"],
    });
    activeChildren.add(child);
    const completed = new Promise((resolve, reject) => {
      child.once("error", () => reject(new SafeFailure(502, "model_failed", "The model request failed")));
      child.once("exit", (code, signal) => resolve({ code, signal }));
    });
    child.stdin.on("error", () => {});
    child.stdin.end(envelope);
    const expired = new Promise((_, reject) => {
      timeout = setTimeout(() => {
        stopChild(child);
        setTimeout(() => stopChild(child, "SIGKILL"), 2000).unref();
        reject(new SafeFailure(504, "model_timeout", "The model request timed out"));
      }, timeoutMs);
      timeout.unref();
    });
    const result = await Promise.race([completed, expired]);
    if (result.code !== 0 || result.signal) {
      throw new SafeFailure(502, "model_failed", "The model request failed");
    }
    let details;
    try {
      details = await fsp.stat(outputPath);
    } catch {
      throw new SafeFailure(502, "model_failed", "The model response was unavailable");
    }
    if (!details.isFile() || details.size < 1 || details.size > MAX_OUTPUT_BYTES) {
      throw new SafeFailure(502, "invalid_model_output", "The model response was invalid");
    }
    let output;
    try {
      output = JSON.parse(await fsp.readFile(outputPath, "utf8"));
    } catch {
      throw new SafeFailure(502, "invalid_model_output", "The model response was invalid");
    }
    return output;
  } finally {
    if (timeout) clearTimeout(timeout);
    if (child) activeChildren.delete(child);
    await fsp.rm(requestDir, { force: true, recursive: true });
  }
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const evalRoot = await fsp.realpath(options.evalRoot);
  const evalDetails = await fsp.stat(evalRoot);
  if (!evalDetails.isDirectory()) throw new Error("invalid eval root");

  const configuredCodex = (process.env.SBEK_CODEX_BIN ?? DEFAULT_CODEX_BIN).trim();
  if (!path.isAbsolute(configuredCodex)) throw new Error("invalid Codex binary");
  const codexBin = await fsp.realpath(configuredCodex);
  if (!(await fsp.stat(codexBin)).isFile()) throw new Error("invalid Codex binary");
  await fsp.access(codexBin, fs.constants.X_OK);

  const token = randomBytes(32).toString("base64url");
  await fsp.writeFile(options.tokenFile, token, { flag: "wx", mode: 0o600 });

  let activeRequests = 0;
  const activeChildren = new Set();
  const server = http.createServer(async (req, res) => {
    try {
      if (req.method !== "POST" || req.url !== "/v1/generate") {
        throw new SafeFailure(404, "not_found", "The endpoint was not found");
      }
      if (!validBearer(req.headers.authorization, token)) {
        throw new SafeFailure(401, "unauthorized", "Authentication is required");
      }
      const contentType = String(req.headers["content-type"] ?? "").toLowerCase();
      if (!/^application\/json(?:\s*;|$)/.test(contentType)) {
        throw new SafeFailure(415, "unsupported_media_type", "Application JSON is required");
      }
      if (activeRequests >= MAX_CONCURRENT_REQUESTS) {
        throw new SafeFailure(429, "busy", "The bridge is busy");
      }
      activeRequests += 1;
      try {
        let rawPayload;
        try {
          rawPayload = JSON.parse(await readRequestBody(req));
        } catch (error) {
          if (error instanceof SafeFailure) throw error;
          throw new SafeFailure(400, "invalid_json", "The request body is not valid JSON");
        }
        const payload = validatePayload(rawPayload);
        const images = await resolveImages(evalRoot, payload.images);
        const output = await runCodex({
          activeChildren,
          codexBin,
          evalRoot,
          images,
          payload,
          timeoutMs: options.timeoutMs,
        });
        safeJson(res, 200, { output });
      } finally {
        activeRequests -= 1;
      }
    } catch (error) {
      if (error instanceof SafeFailure) safeError(res, error);
      else safeError(res, new SafeFailure(500, "bridge_error", "The bridge request failed"));
    }
  });
  server.requestTimeout = options.timeoutMs + 15_000;
  server.headersTimeout = 10_000;
  server.keepAliveTimeout = 5_000;

  const shutdown = () => {
    server.close();
    for (const child of activeChildren) stopChild(child);
  };
  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(options.port, options.host, resolve);
  });
  const address = server.address();
  if (address === null || typeof address === "string") throw new Error("invalid bridge address");
  await fsp.writeFile(
    options.readyFile,
    `${JSON.stringify({ host: options.host, port: address.port })}\n`,
    { flag: "wx", mode: 0o600 },
  );
}

main().catch(() => {
  process.stderr.write("Codex evaluation bridge could not start.\n");
  process.exit(1);
});
