from app.config.loader import GmailConfig


def test_gmail_config_max_unknown_sender_llm_calls_default():
    assert GmailConfig().max_unknown_sender_llm_calls == 20


def test_gmail_config_max_unknown_sender_llm_calls_override():
    assert GmailConfig(max_unknown_sender_llm_calls=5).max_unknown_sender_llm_calls == 5
