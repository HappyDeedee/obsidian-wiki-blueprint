"""A synthetic, prewritten mechanism test, NOT an LLM curation benchmark."""
import argparse
import json
from pathlib import Path
from wiki import Wiki, WikiError, atomic, digest, note, read_json, save_json

SOURCE = "00-Inbox/随手放/虚构示例.md"
TARGET = "20-Wiki/实体/演示项目.md"
QUOTE = "演示项目计划下周测试，预算未知。"


def page(statement, sources):
    return ("---\ntitle: 演示项目\ndomain: 个人\ntype: 实体\nstatus: 进行中\n"
            "created: 2000-01-01\nupdated: 2000-01-01\nrule_version: public-v1\n"
            "sources: " + json.dumps(sources, ensure_ascii=False) + "\n---\n# 演示项目\n\n"
            + statement + "\n\n示例日期仅为虚构文件整理时间，不是事件发生日。\n\n## 来源\n"
            + "\n".join("- [[" + p + "]]" for p in sources) + "\n")


def run(vault):
    wiki = Wiki(vault, "owner")
    stamp = wiki.state / "demo-result.json"
    if stamp.exists():
        return {**read_json(stamp), "reused_demo": True}
    source_file = note(wiki.root, SOURCE)
    if source_file.exists():
        raise WikiError("A partial demo exists; inspect its transactions, do not overwrite the source")
    with source_file.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("# 虚构资料\n\n" + QUOTE + "\n")
    before = digest(source_file.read_bytes())
    prepared = wiki.prepare(SOURCE, [TARGET])
    initial = {"quotes": [QUOTE], "updates": [{"path": TARGET, "content": page(
        "fact_state: planned\n\n计划下周测试；事件日期及预算未知，不代表已经上线。", [SOURCE])}]}
    first = wiki.commit(prepared["token"], initial)
    replay = wiki.commit(prepared["token"], initial)
    assert replay["reused"]
    wiki.question("demo-001", TARGET, "测试是否已经完成？不知道可以保留未知。")
    answer = wiki.answer("demo-001", 1, "尚未完成测试，仍然是计划。", "虚构演示：模拟用户现场原话")
    duplicate = wiki.answer("demo-001", 1, "尚未完成测试，仍然是计划。", "重复投递")
    assert duplicate["reused"]
    repair = wiki.prepare(answer["path"], [TARGET], "topic_repair")
    plan = {"quotes": [answer["text"]], "updates": [{"path": TARGET, "content": page(
        "fact_state: planned\n\n用户自述尚未完成测试；预算仍未知。身份与业务事实核验是两回事。\n\n"
        "## 历史\n最初来源仅说明计划下周测试，未支持已完成。", [SOURCE, answer["path"]])}]}
    final = wiki.commit(repair["token"], plan)
    q = wiki.resolve("demo-001", answer["answer_id"], final["commit_key"])
    assert q["status"] == "adopted"
    assert before == digest(source_file.read_bytes())
    health = wiki.health()
    assert health["unique_curated_sources"] == 1
    result = {"status": "REFERENCE_DEMO_PASS", "source_unchanged": True,
              "first_commit": first["commit_key"], "answer_commit": final["commit_key"],
              "duplicate_commit_reused": True, "duplicate_answer_reused": True,
              "confirmation_status": q["status"], "unique_curated_sources": 1,
              "model_or_remote_service_tested": False}
    save_json(stamp, result)
    return result


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(Path(args.vault)), ensure_ascii=False, indent=2))
    except (WikiError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
