"""An offline app's immutable model must never become a download destination."""
from app.config.configuration import Configuration
from app.embedding import embed


def test_offline_bundle_overrides_saved_download_permission(tmp_path):
    config = Configuration.load(tmp_path)
    assert config.auto_download_model
    env = config.runtime_environment({'MEMORY_OFFLINE_BUNDLE': '1'})
    assert env['MEMORY_AUTO_DOWNLOAD_MODEL'] == 'false'
    assert env['HF_HUB_OFFLINE'] == env['TRANSFORMERS_OFFLINE'] == '1'
    assert env['SENTENCE_TRANSFORMERS_OFFLINE'] == 'true'
    assert config.auto_download_model  # The user's persisted choice is not rewritten.


def test_offline_bundle_rejects_explicit_download_retry(monkeypatch):
    options = {}
    def capture(**kwargs):
        options.update(kwargs)
        return object()
    monkeypatch.setattr(embed, 'SentenceTransformersEmbedder', capture)
    embed.create_embedder('sentence_transformers', allow_download=True,
                          environment={'MEMORY_OFFLINE_BUNDLE': '1',
                                       'MEMORY_AUTO_DOWNLOAD_MODEL': 'true'})
    assert options['auto_download'] is False
