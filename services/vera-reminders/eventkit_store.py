"""EventKit access layer for the reminders bridge.

EventKit sees every list the signed-in iCloud account participates in, shared
lists included, so items added by Siri on any household device appear here and
writes sync back out. All calls are synchronous; EventKit completion handlers
are bridged with threading.Event."""
import datetime
import threading
from typing import Any

from EventKit import EKEntityTypeReminder, EKEventStore, EKReminder
from Foundation import NSDateComponents, NSDateComponentUndefined

_store: Any = None
_access: bool | None = None
_lock = threading.Lock()


def ensure_access() -> bool:
    global _store, _access
    with _lock:
        if _access:
            return True
        if _store is None:
            _store = EKEventStore.alloc().init()
        done = threading.Event()
        result = {"granted": False}

        def _cb(granted: bool, error: Any) -> None:
            result["granted"] = bool(granted)
            done.set()

        _store.requestFullAccessToRemindersWithCompletion_(_cb)
        done.wait(timeout=120)
        _access = result["granted"]
        return _access


def _calendars() -> list:
    return list(_store.calendarsForEntityType_(EKEntityTypeReminder))


def lists() -> list[dict]:
    return [{"id": str(c.calendarIdentifier()), "name": str(c.title())} for c in _calendars()]


def _find_calendar(name: str) -> Any:
    for c in _calendars():
        if str(c.title()).strip().lower() == name.strip().lower():
            return c
    raise KeyError(name)


def _fetch(cals: list) -> list:
    done = threading.Event()
    out = {"items": []}

    def _cb(items: Any) -> None:
        out["items"] = list(items or [])
        done.set()

    pred = _store.predicateForRemindersInCalendars_(cals)
    _store.fetchRemindersMatchingPredicate_completion_(pred, _cb)
    done.wait(timeout=30)
    return out["items"]


def _val(x: Any) -> int | None:
    return None if x == NSDateComponentUndefined else int(x)


def _components_to_iso(dc: Any) -> str | None:
    if dc is None:
        return None
    y, m, d = _val(dc.year()), _val(dc.month()), _val(dc.day())
    if not (y and m and d):
        return None
    hh, mm = _val(dc.hour()), _val(dc.minute())
    if hh is None:
        return f"{y:04d}-{m:02d}-{d:02d}"
    return f"{y:04d}-{m:02d}-{d:02d}T{hh:02d}:{mm or 0:02d}"


def _iso_to_components(s: str) -> Any:
    dt = datetime.datetime.fromisoformat(s)
    dc = NSDateComponents.alloc().init()
    dc.setYear_(dt.year)
    dc.setMonth_(dt.month)
    dc.setDay_(dt.day)
    if len(s) > 10:
        dc.setHour_(dt.hour)
        dc.setMinute_(dt.minute)
    return dc


def _norm(r: Any) -> dict:
    return {
        "id": str(r.calendarItemIdentifier()),
        "title": str(r.title() or ""),
        "notes": str(r.notes()) if r.notes() else None,
        "due": _components_to_iso(r.dueDateComponents()),
        "completed": bool(r.isCompleted()),
        "list": str(r.calendar().title()) if r.calendar() else None,
    }


def reminders(list_name: str | None = None, completed: bool = False) -> list[dict]:
    cals = [_find_calendar(list_name)] if list_name else _calendars()
    items = [_norm(r) for r in _fetch(cals)]
    return [i for i in items if i["completed"] == completed]


def _save(r: Any) -> dict:
    ok, err = _store.saveReminder_commit_error_(r, True, None)
    if not ok:
        raise RuntimeError(str(err))
    return _norm(r)


def _sentence_case(title: str) -> str:
    t = title.strip()
    return t[:1].upper() + t[1:] if t else t


def create(list_name: str, title: str, notes: str | None = None, due: str | None = None) -> dict:
    cal = _find_calendar(list_name)
    r = EKReminder.reminderWithEventStore_(_store)
    r.setTitle_(_sentence_case(title))
    r.setCalendar_(cal)
    if notes:
        r.setNotes_(notes)
    if due:
        r.setDueDateComponents_(_iso_to_components(due))
    return _save(r)


def update(rid: str, completed: bool | None = None, title: str | None = None,
           notes: str | None = None, due: str | None = None) -> dict:
    r = _store.calendarItemWithIdentifier_(rid)
    if r is None:
        raise KeyError(rid)
    if completed is not None:
        r.setCompleted_(completed)
    if title is not None:
        r.setTitle_(title)
    if notes is not None:
        r.setNotes_(notes)
    if due is not None:
        r.setDueDateComponents_(_iso_to_components(due))
    return _save(r)
