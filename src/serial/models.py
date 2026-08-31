from sqlalchemy import text
from sqlalchemy.orm import Session


def get_next_global_serial_number(db: Session) -> int:
    try:
        result = db.execute(text("SELECT nextval('global_work_item_serial_seq')"))
        next_val = result.scalar()

        if next_val and next_val > 0:
            return next_val
    except Exception:
        pass

    max_task = db.execute(
        text("SELECT COALESCE(MAX(serial_number), 0) FROM tasks")
    ).scalar() or 0

    max_story = db.execute(
        text("SELECT COALESCE(MAX(serial_number), 0) FROM user_stories")
    ).scalar() or 0

    return max(max_task, max_story) + 1


def format_serial_number(seq: int) -> str:
    if seq <= 0:
        return ""

    return f"#{seq}"