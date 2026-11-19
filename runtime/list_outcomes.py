"""Truthful, bounded presentation of partially processed vector calls."""
from collections import Counter


def reasons(rows):
    """Closed categories: no unbounded paths or internal exception strings."""
    from messages import get as message
    groups = Counter()
    for row in rows:
        code = str(row.get("error_code") or row.get("error_class") or "")
        category = ("TIME" if code in {"timeout", "remote_timeout", "ERR_FS_REQUEST_EXPIRED",
                    "ERR_FS_REQUEST_TIMEOUT", "ERR_FS_INVOCATION_INVALID"} else
                    "PERMISSION" if code in {"permission_denied", "ERR_FS_PERMISSION_REQUIRED"} else
                    "CHANGED" if code == "ERR_FS_SOURCE_CHANGED" else
                    "FIXED" if code in {"unsafe_target", "ERR_REFUSE_MOVE"} else "OTHER")
        groups[category] += 1
    return "; ".join(message("MSG_LIST_CAUSE_" + name, count=count)
                     for name, count in sorted(groups.items()))


def normalize(value):
    """Show confirmed counts without changing the authoritative result status.

    The counts are the tool's confirmed counts. Results without a vector
    receipt, or without a successful item, retain their original semantics.
    """
    if not isinstance(value, dict): return value
    done, failed = value.get("ok_count"), value.get("fail_count")
    if (type(done) is not int or type(failed) is not int or done <= 0 or failed <= 0
            or not any(isinstance(value.get(key), list) for key in ("results", "entries", "items"))
            or value.get("decision") == "accepted"):
        return value
    from messages import get as message
    text = message("MSG_LIST_PARTIAL", done=done, total=done+failed)
    failures = value.get("failed") if isinstance(value.get("failed"), list) else []
    causes = reasons([row for row in failures if isinstance(row, dict)])
    if causes: text += " " + causes
    reported = len(failures)
    if reported < failed:
        text += " " + message("MSG_LIST_UNREPORTED", count=failed-reported)
        if value.get("_list_unreported_reason"):
            text += " " + value["_list_unreported_reason"]
    result = {**value, "partial": True, "final_message_hint": text,
              "_list_partial_notice": text}
    return result
