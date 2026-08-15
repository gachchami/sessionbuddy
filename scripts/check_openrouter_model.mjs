const modelId = process.argv[2] || "stealth/ox-alpha";
const apiKey = process.env.OPENROUTER_API_KEY;
if (!apiKey) throw new Error("OPENROUTER_API_KEY is required");

const response = await fetch("https://openrouter.ai/api/v1/models", {
  headers: { Authorization: `Bearer ${apiKey}` },
});
if (!response.ok) {
  throw new Error(`OpenRouter model preflight failed with HTTP ${response.status}`);
}

const payload = await response.json();
const model = payload.data?.find((candidate) => candidate.id === modelId);
if (!model) throw new Error(`OpenRouter model is unavailable: ${modelId}`);

const promptPrice = Number(model.pricing?.prompt);
const completionPrice = Number(model.pricing?.completion);
if (promptPrice !== 0 || completionPrice !== 0) {
  throw new Error(
    `${modelId} is no longer free (prompt=${model.pricing?.prompt}, completion=${model.pricing?.completion}); refusing to start a paid eval`,
  );
}

const parameters = new Set(model.supported_parameters ?? []);
for (const required of ["tools", "response_format", "reasoning_effort"]) {
  if (!parameters.has(required)) {
    throw new Error(`${modelId} no longer supports required parameter: ${required}`);
  }
}
const efforts = model.reasoning?.supported_efforts ?? [];
if (!efforts.includes("max")) {
  throw new Error(`${modelId} no longer supports max reasoning effort`);
}

console.log(
  `OpenRouter preflight ready: ${modelId}, free input/output, tools + response format + max reasoning`,
);
