from types import SimpleNamespace

from src.utils import storage


def test_attachment_keys_use_expected_prefix_and_sanitize_filename():
    key = storage.build_attachment_key(
        "tasks",
        "01a06000-0001-7000-8000-000000000001",
        "../screen shot.png",
    )

    assert key.startswith(
        "attachments/tasks/01a06000-0001-7000-8000-000000000001/"
    )
    assert key.endswith("_screen_shot.png")
    assert "work_pilot_bucket" not in key


def test_comment_and_logo_keys_use_expected_prefixes():
    comment_key = storage.build_attachment_key("comments", "comment-1", "file.pdf")
    story_key = storage.build_attachment_key("user_stories", "story-1", "spec.docx")
    logo_key = storage.build_logo_key("organization-1", "logo.png")

    assert comment_key.startswith("attachments/comments/comment-1/")
    assert story_key.startswith("attachments/user_stories/story-1/")
    assert logo_key.startswith("organizations/logos/organization-1/")


def test_upload_uses_same_key_for_s3_and_public_url(monkeypatch):
    calls = {}

    class FakeClient:
        def upload_fileobj(self, file_obj, bucket, key, ExtraArgs):
            calls.update(
                bucket=bucket,
                key=key,
                content=file_obj.read(),
                extra_args=ExtraArgs,
            )

    settings = SimpleNamespace(
        s3_bucket="work_pilot_bucket",
        s3_public_endpoint="https://example.supabase.co/storage/v1/object/public",
        s3_endpoint="https://example.storage.supabase.co/storage/v1/s3",
    )
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    monkeypatch.setattr(storage, "get_s3_client", lambda: FakeClient())
    monkeypatch.setattr(storage, "validate_s3_configuration", lambda: None)

    key = "attachments/tasks/task-1/unique_file.png"
    url = storage.upload_s3_object(b"content", key, "image/png")

    assert calls == {
        "bucket": "work_pilot_bucket",
        "key": key,
        "content": b"content",
        "extra_args": {"ContentType": "image/png"},
    }
    assert url == (
        "https://example.supabase.co/storage/v1/object/public/"
        "work_pilot_bucket/attachments/tasks/task-1/unique_file.png"
    )
