from types import SimpleNamespace

import pytest

from bot.services import author_publication_service, pdf_delivery_service
from bot.services.errors import ServiceError
from bot.state_machine.states import KukaiState
from bot.ui.admin_panel_view import KukaiAdminPanelView, PdfSendConfirmView


def _panel(*, state=KukaiState.RESULTS, mode="manual", reveal=False, published=True):
    return KukaiAdminPanelView(
        kukai_id=1,
        user_id=2,
        state=state,
        author_publication_mode=mode,
        author_reveal=reveal,
        has_published_submissions=published,
    )


def _button(view, label):
    return next(item for item in view.children if getattr(item, "label", None) == label)


def test_admin_panel_export_buttons_follow_stage_and_manual_author_mode():
    before_results = _panel(state=KukaiState.SELECTING_CLOSED, published=False)

    assert _button(before_results, "投句一覧PDFを送信").disabled is True
    assert _button(before_results, "結果PDFを送信").disabled is True
    assert _button(before_results, "作者を公開").disabled is True

    results = _panel()
    assert _button(results, "投句一覧PDFを送信").disabled is False
    assert _button(results, "結果PDFを送信").disabled is False
    assert _button(results, "作者を公開").disabled is False


def test_author_button_only_exists_for_manual_mode_and_becomes_completed():
    automatic = _panel(mode="with_result", reveal=True)
    assert not any("作者" in str(getattr(item, "label", "")) for item in automatic.children)

    revealed = _panel(reveal=True)
    assert _button(revealed, "作者公開済み").disabled is True


def test_pdf_confirmation_defaults_anonymous_and_only_offers_named_when_allowed():
    anonymous = PdfSendConfirmView(kukai_id=1, user_id=2, kind="submission", can_named=False)
    named = PdfSendConfirmView(kukai_id=1, user_id=2, kind="result", can_named=True)

    assert [option.value for option in anonymous.children[0].options] == ["anonymous"]  # type: ignore[attr-defined]
    assert [option.value for option in named.children[0].options] == ["anonymous", "named"]  # type: ignore[attr-defined]
    assert named.show_author is False


def test_reveal_authors_enforces_state_mode_and_duplicate():
    early = SimpleNamespace(
        state=KukaiState.SELECTING_CLOSED.value,
        author_publication_mode="manual",
        author_reveal=False,
    )
    with pytest.raises(ServiceError, match="結果公開後"):
        author_publication_service.reveal_authors(early)

    never = SimpleNamespace(
        state=KukaiState.RESULTS.value,
        author_publication_mode="never",
        author_reveal=False,
    )
    with pytest.raises(ServiceError, match="公開はしない"):
        author_publication_service.reveal_authors(never)

    manual = SimpleNamespace(
        state=KukaiState.RESULTS.value,
        author_publication_mode="manual",
        author_reveal=False,
    )
    author_publication_service.reveal_authors(manual)
    assert manual.author_reveal is True
    assert author_publication_service.reveal_authors(manual) is False


@pytest.mark.asyncio
async def test_pdf_delivery_uses_attachment_below_limit(monkeypatch):
    sent = []

    class _Channel:
        async def send(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(pdf_delivery_service, "DISCORD_MAX_BYTES", 10)
    await pdf_delivery_service.send_pdf_to_channel(
        _Channel(),
        pdf_bytes=b"pdf",
        filename="result.pdf",
        kukai_id=1,
    )

    assert sent[0]["file"].filename == "result.pdf"


@pytest.mark.asyncio
async def test_pdf_delivery_uses_temp_url_above_limit(monkeypatch):
    sent = []

    class _Channel:
        async def send(self, **kwargs):
            sent.append(kwargs)

    async def fake_publish(*args):
        return "https://example.test/result.pdf"

    monkeypatch.setattr(pdf_delivery_service, "DISCORD_MAX_BYTES", 2)
    monkeypatch.setattr(pdf_delivery_service.pdf_service, "publish_temp", fake_publish)
    await pdf_delivery_service.send_pdf_to_channel(
        _Channel(),
        pdf_bytes=b"pdf",
        filename="result.pdf",
        kukai_id=1,
    )

    assert "https://example.test/result.pdf" in sent[0]["content"]
