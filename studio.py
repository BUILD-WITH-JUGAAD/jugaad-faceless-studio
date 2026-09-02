"""Launch the JUGAAD studio in a browser."""

import os

# Pin BLAS before any numpy-using import. Apple's Python.app gives a
# 64KB stack and OpenBLAS SIGSEGVs (exit -11) if it spawns gemm threads.
for _k, _v in (
    ("OPENBLAS_NUM_THREADS", "1"),
    ("OMP_NUM_THREADS", "1"),
    ("MKL_NUM_THREADS", "1"),
    ("VECLIB_MAXIMUM_THREADS", "1"),
    ("NUMEXPR_NUM_THREADS", "1"),
    ("TOKENIZERS_PARALLELISM", "false"),
):
    os.environ[_k] = _v

from studio.app import app


def main():
    import uvicorn

    port = int(os.getenv("PORT", "8787"))
    host = os.getenv("HOST", "0.0.0.0" if os.getenv("RENDER") else "127.0.0.1")
    print("\n  JUGAAD studio  →  http://{0}:{1}\n".format(host, port))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
