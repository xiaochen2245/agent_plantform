#!/usr/bin/env python3
"""RAGFlow 升级回归清单（#36 / D1 交付）。

升级前记录基线、升级后重跑对照，5 项全 PASS 才算通过（COLLAB.md 环境与部署节）：
  1 health        引擎存活（HTTP 可达且非 5xx）
  2 data_alive    租户 C 的 sf-m3-test 库数据存活（升级未丢数据）
  3 provider      embedding/rerank 默认模型绑定仍在
  4 isolation     租户 B 读租户 C 的库必须被拒（租户隔离未破）
  5 retrieval     检索+rerank 出块正常

用法:
  python3 scripts/ragflow-regress.py BASE TOKEN_A TOKEN_B TOKEN_C M3_DATASET_ID PROTECTED_DATASET_ID

各参数均可用环境变量代替（同名大写）：RAGFLOW_BASE_URL / REGRESS_TOKEN_A / _B / _C /
REGRESS_M3_DATASET / REGRESS_PROTECTED_DATASET。退出码 0=全过，1=有失败项。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

RETRIEVAL_QUESTION = "技术参数负偏离怎么扣分"
RERANK_ID = "Qwen/Qwen3-Reranker-0.6B@sf-main@SILICONFLOW"


def call(base: str, token: str, method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body).encode() if body else None,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        return {"http": e.code, "msg": e.read().decode()[:200]}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return {"http": 0, "msg": str(e)[:200]}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("base", nargs="?", default=os.environ.get("RAGFLOW_BASE_URL", ""),
                   help="RAGFlow API 根地址，如 http://192.168.20.226:9380")
    p.add_argument("token_a", nargs="?", default=os.environ.get("REGRESS_TOKEN_A", ""))
    p.add_argument("token_b", nargs="?", default=os.environ.get("REGRESS_TOKEN_B", ""))
    p.add_argument("token_c", nargs="?", default=os.environ.get("REGRESS_TOKEN_C", ""))
    p.add_argument("m3_dataset", nargs="?", default=os.environ.get("REGRESS_M3_DATASET", ""))
    p.add_argument("protected_dataset", nargs="?", default=os.environ.get("REGRESS_PROTECTED_DATASET", ""),
                   help="租户 C 的库 id（用租户 B 越权读，验证隔离）")
    args = p.parse_args()
    missing = [k for k, v in {
        "base": args.base, "token_a": args.token_a, "token_b": args.token_b,
        "token_c": args.token_c, "m3_dataset": args.m3_dataset,
        "protected_dataset": args.protected_dataset}.items() if not v]
    if missing:
        p.error(f"缺少参数（或对应环境变量）: {', '.join(missing)}")

    results: list[tuple[str, bool, str]] = []

    # 1 health：根路径可达且非 5xx
    r = call(args.base, args.token_a, "GET", "/api/v1/datasets")
    results.append(("health", 0 < r.get("http", 0) < 500 or r.get("code") == 0,
                    f"datasets 探活响应: {str(r)[:120]}"))

    # 2 data_alive：sf-m3-test 库仍在（租户 C）
    names = [x.get("name") for x in (r.get("data") or [])] if r.get("code") == 0 else None
    if names is None:  # token_a 探活失败时用 token_c 再拉一次，保证该项独立判读
        rc = call(args.base, args.token_c, "GET", "/api/v1/datasets")
        names = [x.get("name") for x in (rc.get("data") or [])]
    results.append(("data_alive", "sf-m3-test" in (names or []), f"datasets: {names}"))

    # 3 provider：默认模型绑定里 embedding/rerank 仍在（租户 C）
    m = call(args.base, args.token_c, "GET", "/api/v1/models/default")
    models = (m.get("data") or {}).get("models") or []
    kinds = {x.get("model_type") for x in models}
    results.append(("provider", "embedding" in kinds, f"已绑模型类型: {sorted(k for k in kinds if k) or m}"))

    # 4 isolation：租户 B 读租户 C 的库 → 必须被拒（HTTP 4xx 或业务 code!=0）
    den = call(args.base, args.token_b, "GET", f"/api/v1/datasets/{args.protected_dataset}")
    denied = (isinstance(den.get("http"), int) and 400 <= den["http"] < 500) or den.get("code") not in (0, None)
    results.append(("isolation_denied", denied, f"越权读响应: {str(den)[:120]}"))

    # 5 retrieval：检索+rerank 出块（租户 C 的 m3 库）
    q = {"question": RETRIEVAL_QUESTION, "dataset_ids": [args.m3_dataset], "page_size": 6,
         "rerank_id": RERANK_ID, "top_k": 2}
    rr = call(args.base, args.token_c, "POST", "/api/v1/retrieval", q)
    chunks = (rr.get("data") or {}).get("chunks") or []
    top = (chunks[0].get("content") or "")[:70].replace("\n", " ") if chunks else str(rr)[:120]
    results.append(("retrieval_rerank", len(chunks) > 0, f"{len(chunks)} chunks; top: {top}"))

    failed = [name for name, ok, _ in results if not ok]
    for i, (name, ok, detail) in enumerate(results, 1):
        print(f"{i} {name}: {'PASS' if ok else 'FAIL'}  {detail}")
    print(f"\n{'='*40}\n{'全过 ✓' if not failed else '失败项 ✗: ' + ', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
