# Pinned BGE-M3 configuration fixtures

These exact JSON bytes come from the existing production model BAAI/bge-m3,
revision 5617a9f61b028005a4858fdac845db406aefb181. Each is checked against the
pre-existing SHA256 and size in docker/bge-m3-manifest.json. No model weights.

- https://huggingface.co/BAAI/bge-m3/resolve/5617a9f61b028005a4858fdac845db406aefb181/config.json
- https://huggingface.co/BAAI/bge-m3/resolve/5617a9f61b028005a4858fdac845db406aefb181/1_Pooling/config.json

Used to regress the first Setup run's false rejection of model config.json as
user configuration. Model license notices remain under docker/licenses/.
