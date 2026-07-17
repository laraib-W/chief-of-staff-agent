from app.config.loader import (
    Config,
    GmailConfig,
    IdentityConfig,
    LLMConfig,
    PlaneConfig,
    ThresholdsConfig,
)
from app.providers import llm


def _config() -> Config:
    return Config(
        identity=IdentityConfig(
            user_name="T", timezone="UTC", delivery_address="t@x.com"
        ),
        gmail=GmailConfig(),
        plane=PlaneConfig(project_ids=["p1"]),
        thresholds=ThresholdsConfig(),
        llm=LLMConfig(model="claude-test-model"),
        config_hash="x",
    )


class _FakeTextBlock:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class _FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeMessage("yes")


class _FakeAnthropic:
    def __init__(self, *args, **kwargs):
        self.messages = _FakeMessages()


def test_complete_passes_model_and_prompt(monkeypatch):
    fake_client = _FakeAnthropic()
    monkeypatch.setattr(llm.anthropic, "Anthropic", lambda: fake_client)

    result = llm.complete(_config(), "Is this spam?", max_tokens=50)

    assert result == "yes"
    call = fake_client.messages.calls[0]
    assert call["model"] == "claude-test-model"
    assert call["max_tokens"] == 50
    assert call["messages"] == [{"role": "user", "content": "Is this spam?"}]


def test_complete_default_max_tokens(monkeypatch):
    fake_client = _FakeAnthropic()
    monkeypatch.setattr(llm.anthropic, "Anthropic", lambda: fake_client)

    llm.complete(_config(), "prompt")

    assert fake_client.messages.calls[0]["max_tokens"] == 200
