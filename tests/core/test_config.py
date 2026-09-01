import pytest

from qcom.core.config import AppConfig, load_config, load_dotenv
from qcom.core.errors import ConfigError

YAML = """
version: 2
throttle: {min_gap_s: 1, jitter_s: 0}
concurrency: {max_platforms_in_parallel: 2, blinkit: 3}
proxy: {label: acme}
"""


def test_load_and_hash(tmp_path, monkeypatch):
    for k in ("QCOM_PROXY_SERVER", "QCOM_PROXY_USERNAME", "QCOM_PROXY_PASSWORD", "QCOM_PROXY_FALLBACK_SERVERS"):
        monkeypatch.delenv(k, raising=False)
    p = tmp_path / "config.yaml"
    p.write_text(YAML)
    cfg = load_config(p, env_path=tmp_path / ".env")
    assert cfg.throttle.min_gap_s == 1
    assert cfg.concurrency.contexts_for("blinkit") == 3
    assert cfg.concurrency.contexts_for("zepto") == 1
    assert cfg.proxy.label == "acme" and not cfg.proxy.configured
    h1 = cfg.config_hash()
    assert h1 == load_config(p, env_path=tmp_path / ".env").config_hash()


def test_secrets_come_from_env_and_never_hash(tmp_path, monkeypatch):
    for k in ("QCOM_PROXY_SERVER", "QCOM_PROXY_USERNAME", "QCOM_PROXY_PASSWORD", "QCOM_PROXY_FALLBACK_SERVERS"):
        monkeypatch.delenv(k, raising=False)
    p = tmp_path / "config.yaml"
    p.write_text(YAML)
    env = tmp_path / ".env"
    env.write_text("QCOM_PROXY_SERVER=http://p:1\nQCOM_PROXY_USERNAME=u\nQCOM_PROXY_PASSWORD='s3cret'\nQCOM_PROXY_FALLBACK_SERVERS=http://p:2, http://p:3\n# comment\n")
    cfg = load_config(p, env_path=env)
    assert cfg.proxy.configured and cfg.proxy.password == "s3cret"
    assert cfg.proxy.fallback_servers == ["http://p:2", "http://p:3"]
    public = cfg.public_dict()
    assert "s3cret" not in str(public) and "http://p:1" not in str(public)
    assert public["proxy"] == {"label": "acme", "configured": True}


def test_proxy_secret_in_yaml_is_refused(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("proxy: {server: http://x}\n")
    with pytest.raises(ConfigError, match="QCOM_PROXY_SERVER"):
        load_config(p, env_path=tmp_path / ".env")


def test_missing_and_invalid(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")
    p = tmp_path / "bad.yaml"
    p.write_text("run: {max_failure_rate: 5}\n")
    with pytest.raises(ConfigError):
        load_config(p, env_path=tmp_path / ".env")
    bad_env = tmp_path / ".env"
    bad_env.write_text("NOEQUALS\n")
    with pytest.raises(ConfigError):
        load_dotenv(bad_env)


def test_defaults_match_documented_policy_table():
    cfg = AppConfig()
    assert cfg.retry.network_timeout.attempts == 3
    assert cfg.retry.rate_limited.backoff_base_s == 60
    assert cfg.retry.location_not_set.attempts == 2
    assert cfg.retry.unknown.attempts == 1
    assert cfg.circuit_breaker.consecutive_failures == 5
