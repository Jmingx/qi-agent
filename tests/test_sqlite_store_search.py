import uuid
from pathlib import Path

from qi_agent.storage.sqlite_store import SQLiteStore


def _db_path(name: str) -> Path:
    root = Path.home() / "AppData" / "Local" / "Temp" / "qi-agent-tests"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{name}-{uuid.uuid4().hex}.db"


def test_search_messages_escapes_like_and_caps_at_fifty() -> None:
    store = SQLiteStore(db_path=str(_db_path("sqlite-search")))

    store.create_session("sess-percent", "Percent")
    store.append_message("sess-percent", {"role": "user", "content": "cost 100% done"})
    store.create_session("sess-percent-2", "Other")
    store.append_message("sess-percent-2", {"role": "user", "content": "cost 100X done"})
    percent_results = store.search_messages("100%")
    assert len(percent_results) == 1
    assert percent_results[0]["session_id"] == "sess-percent"
    assert percent_results[0]["content"] == "cost 100% done"

    store.create_session("sess-underscore", "Under")
    store.append_message("sess-underscore", {"role": "user", "content": "code a_b"})
    store.create_session("sess-underscore-2", "Other")
    store.append_message("sess-underscore-2", {"role": "user", "content": "code aXb"})
    underscore_results = store.search_messages("a_b")
    assert len(underscore_results) == 1
    assert underscore_results[0]["session_id"] == "sess-underscore"

    for index in range(60):
        session_id = f"sess-bulk-{index}"
        store.create_session(session_id, f"Bulk {index}")
        store.append_message(
            session_id,
            {"role": "user", "content": f"needle {index}"},
        )

    limited = store.search_messages("needle")
    assert len(limited) == 50
