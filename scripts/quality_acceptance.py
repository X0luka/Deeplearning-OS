"""Quality acceptance on synthetic material using unchanged product business code."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for component in ("AgentOS-K", "PaperReader", "GitHub-Wiki"):
    sys.path.insert(0, str(ROOT / "components" / component))

MATERIAL = """Synthetic study: a document processing service writes each completed page to a checkpoint.
On a 120-document test set, the baseline completed 108 documents and the checkpoint version completed 116.
Both used the same CPU and a 2 GB worker memory budget. Median latency was 20 seconds versus 23 seconds.
Four remaining failures were encrypted documents. The experiment did not test scanned handwriting or files above 200 pages.
When the worker exceeds its memory budget, the current page range is split. Already verified pages are reused.
A network timeout during translation is marked uncertain; it does not prove that the provider did not charge.
The objective is $L = L_{text} + 0.2 L_{layout}$. A checkpoint preserves work; it does not repair incorrect text."""
QUESTION = """请根据下面的合成实验资料写一份有分析深度的中文评审：说明完成率提升的绝对百分点和相对变化、性能代价、为什么断点恢复不等于解析正确、证据支持不了哪些结论，以及下一轮最有价值的验证。把事实与建议分开，解释清楚即可，不要求压缩成短答。\n\n""" + MATERIAL


def write(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case", choices=["assistant", "translation", "wiki", "tools"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--output-budget", type=int)
    ap.add_argument("--reason", default="")
    args = ap.parse_args()
    os.umask(0o077)
    from aos.desktop_instance import plan
    from aos.instance_guard import instance_lock
    from aos.llm.store import ModelStore
    value = plan(ROOT, "dev-api-settings", (18777,18778,18779))
    folder = ROOT / ".local/preparation/quality-acceptance"
    folder.mkdir(exist_ok=True)
    with instance_lock(ROOT, value.instance):
        models = ModelStore(value.path("workbench") / "llm")
        settings = models.resolve("assistant", "chat", include_secret=True)
        if settings["base_url"] != "https://api.deepseek.com" or settings["model"] != "deepseek-flash" or settings["protocol"] != "chat":
            raise SystemExit("Unexpected provider; no request")
        if settings.get("custom"):
            raise SystemExit("Private custom prompt excluded from synthetic test")
        if args.output_budget:
            if not args.reason or not 3000 <= args.output_budget <= 65536:
                raise SystemExit("Output adjustment requires a concrete diagnosis")
            settings = {**settings, "max_output_tokens": args.output_budget}
        if args.case == "assistant":
            from aos.assistant.context import token_count
            token_count(QUESTION, settings)
        if args.case == "tools":
            settings = {**settings, "tools": True}
        public = {k:settings.get(k) for k in ("model","protocol","max_output_tokens","thinking","tools","quality_review")}
        if args.dry_run:
            print(json.dumps({"case":args.case,"settings":public,"material_chars":len(MATERIAL),"calls":0}))
            return
        ledger = folder / "attempts.json"
        record = json.loads(ledger.read_text()) if ledger.exists() else {"authorization":"user: quality first, reasonable nonredundant DeepSeek tests on synthetic material", "attempts":[]}
        previous = [a for a in record["attempts"] if a["case"] == args.case]
        if previous and not args.reason:
            raise SystemExit("Case already attempted; diagnosis required, no automatic replay")
        if len(record["attempts"]) >= 8:
            raise SystemExit("Batch review required before more requests")
        entry = {"case":args.case,"state":"reserved","settings":public,"reason":args.reason,"calls":[]}
        record["attempts"].append(entry)
        write(ledger,record)
        started=time.monotonic()
        try:
            if args.case == "assistant":
                from aos.assistant.service import Service
                from aos.assistant.store import Store
                from aos.assistant.transport import stream
                class Settings:
                    def resolve(self,*args,**kwargs):
                        return settings
                store=Store(folder / "assistant.db")
                conversation=store.create("Synthetic quality review","general",[])
                turn,_=store.enqueue(conversation["id"], "quality-"+str(len(record["attempts"])), QUESTION,
                    {"resources":[],"settings":{},"model_scope":"assistant"})
                async def measured(cfg,messages,tools,emit):
                    if len(entry["calls"]) >= 4:
                        raise ValueError("quality_batch_review_required")
                    call={"state":"reserved"}
                    entry["calls"].append(call);write(ledger,record)
                    message,usage=await stream(cfg,messages,tools,emit)
                    call.update(state="completed",usage=usage);write(ledger,record)
                    return message,usage
                asyncio.run(Service(store,Settings(),{},None,measured).run(turn))
                result=store.turn(turn)
                entry.update(state=result["state"],error=result.get("error"),answer_chars=len(result.get("answer","")))
                answer=result.get("answer","")
                entry["reopened_answer_equal"]=Store(store.path).turn(turn)["answer"]==answer
            elif args.case == "tools":
                from aos.assistant.transport import stream
                from aos.assistant.service import TOOL
                cfg={**settings,"tools":True}
                async def tool_flow():
                    messages=[{"role":"user","content":"请使用 read_material 读取 provider=synthetic、resource=checkpoint 的状态，再用中文解释已完成部分能否保留以及是否能断言翻译超时未计费。"}]
                    async def discard(_):
                        pass
                    for step in range(2):
                        call={"state":"reserved"}
                        entry["calls"].append(call);write(ledger,record)
                        message,usage=await stream(cfg,messages,[TOOL],discard)
                        call.update(state="completed",usage=usage);write(ledger,record)
                        calls=message.get("tool_calls",[])
                        if step==0:
                            if len(calls)!=1 or calls[0]["function"]["name"]!="read_material":
                                raise ValueError("expected_one_read_material")
                            arguments=json.loads(calls[0]["function"]["arguments"])
                            if arguments.get("provider")!="synthetic" or arguments.get("resource")!="checkpoint":
                                raise ValueError("unexpected_tool_scope")
                            messages.extend([message,{"role":"tool","tool_call_id":calls[0]["id"],"name":"read_material",
                                "content":json.dumps({"completed_pages":[1,2],"current_page":3,"state":"paused","error":"memory_limit","translation_request":"uncertain","provider_billing":"unknown"})}])
                            entry["thinking_tool_field_present"]=bool(message.get("reasoning_content"))
                        else:
                            if calls or not message.get("content"):
                                raise ValueError("missing_final_answer")
                            return message["content"]
                answer=asyncio.run(tool_flow())
                entry.update(state="completed",answer_chars=len(answer))
            elif args.case == "wiki":
                from wiki_app import chat
                from wiki_app.store import Store
                cfg={**settings,"source":"agentos","protocol":"openai","enabled":True}
                store=Store(folder/("wiki-"+str(len(record["attempts"]))))
                repo,version="synthetic-repo","synthetic-version"
                files={
                    "README.md":"# Synthetic checkpoint service\nRetries preserve completed pages.\n",
                    "checkpoint.py":"def remaining(pages, done):\n    return [p for p in pages if p not in done]\n\ndef accept(done, page, valid):\n    if valid:\n        done.add(page)\n    return done\n",
                    "billing.py":"def retry_allowed(state):\n    return state == 'not_sent'\n"}
                with store.db() as db:
                    db.execute("INSERT INTO repos VALUES(?,?,?,?)",(repo,"https://github.com/example/synthetic-quality","main",None))
                manifest={"included":len(files),"files":[{"path":p,"lines":len(t.splitlines())} for p,t in files.items()]}
                store.publish(version,repo,"a"*40,"source",manifest,[],
                    [(version,p,1,len(t.splitlines()),t) for p,t in files.items()])
                chat.config=lambda *a,**k:cfg
                async def measured_wiki(config,messages,allow_tools):
                    call={"state":"reserved"}
                    entry["calls"].append(call);write(ledger,record)
                    message=await chat.completion(config,messages,allow_tools)
                    call.update(state="completed",usage_unavailable_in_product_client=True)
                    write(ledger,record)
                    return message
                question="根据当前源码解释 remaining、accept 和 retry_allowed 如何配合，举一个 done={1,3}、pages=[1,2,3,4] 的例子，并说明这段代码是否保证断电后的恢复和超时后不重复收费。引用实际源码；把源码行为与改进建议区分开。"
                result=asyncio.run(chat.discuss(store,{"version":version,"conversation":"quality","question":question},"quality-job",measured_wiki))
                row=chat.history(store,version,"quality")[0]
                answer=row["answer"]
                reopened=chat.history(Store(store.root),version,"quality")[0]
                entry.update(state="completed",answer_chars=len(answer),citations=len(row["citations"]),
                    reopened_answer_equal=reopened["answer"]==answer,interaction_saved=bool(result["interaction_id"]))
            else:
                from paper_reader.translations import translate_segment, translation_settings
                cfg=translation_settings({**settings,"source":"agentos","protocol":"openai"})
                entry["calls"].append({"state":"reserved"});write(ledger,record)
                result=translate_segment(cfg,MATERIAL)
                answer=result.get("text","")
                entry.update(state=result["state"],error=result.get("error"),answer_chars=len(answer))
                entry["calls"][-1].update(state=result["state"],usage=result.get("usage"))
                entry["formula_preserved"]="$L = L_{text} + 0.2 L_{layout}$" in answer
            # Only synthetic final answer is saved, never headers, keys or reasoning.
            (folder / (args.case+"-"+str(len(record["attempts"]))+".md")).write_text(answer)
        except Exception as exc:
            entry.update(state="failed",error=str(exc) if isinstance(exc,ValueError) and str(exc).replace("_","").isalnum() and len(str(exc))<80 else type(exc).__name__)
        entry["seconds"]=round(time.monotonic()-started,2)
        write(ledger,record)
        print(json.dumps(entry,ensure_ascii=False))
        if entry["state"] != "completed":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
