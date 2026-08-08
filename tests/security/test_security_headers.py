from sessionbuddy.security import content_security_policy


class _Environment:
    CLOUDFLARE_ACCOUNT_ID = "11b5e48aa7648b805e4602b5e980beb2"


def test_csp_allows_only_the_configured_account_r2_endpoint() -> None:
    policy = content_security_policy(_Environment())

    expected = (
        "connect-src 'self' "
        "https://11b5e48aa7648b805e4602b5e980beb2.r2.cloudflarestorage.com"
    )
    assert expected in policy
    assert "object-src 'none'" in policy


def test_csp_rejects_invalid_account_ids_instead_of_interpolating_them() -> None:
    environment = type(
        "Environment",
        (),
        {"CLOUDFLARE_ACCOUNT_ID": "bad.example; script-src *"},
    )()

    policy = content_security_policy(environment)

    assert "connect-src 'self';" in policy
    assert "bad.example" not in policy
