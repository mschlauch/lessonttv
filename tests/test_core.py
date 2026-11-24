import sys, time, threading, os, psutil
sys.path.insert(0,"/home/claude/localtts")
import localtts_core as c
def worker_rss(s):
    try: return psutil.Process(s.proc.pid).memory_info().rss/1e6
    except Exception: return -1
if __name__=="__main__":
    import multiprocessing as mp; mp.freeze_support()
    # catalog sanity
    from kokoro_onnx import Kokoro
    k=Kokoro(c.kokoro_paths()[0],c.kokoro_paths()[1]); have=set(k.get_voices())
    miss=[v["name"] for L in c.VOICES.values() for v in L if v["engine"]=="kokoro" and v["name"] not in have]
    print("kokoro voices missing from model:",miss); del k
    print("split:",c.split_text("Hello there. This is two sentences!\n\nNew paragraph here.",30))
    for lvl in ("light","balanced","fast"): print(lvl,"->",c.perf_description(lvl))
    s=c.Synth()
    cases=[("en","af_heart","light","This is a test. It has several short sentences. Each one is synthesized separately. Memory should stay flat."),
           ("pt-BR","pf_dora","balanced","Olá, este é um teste da síntese de voz em português do Brasil."),
           ("es","ef_dora","balanced","Hola, esto es una prueba de voz en español."),
           ("de","eva_k","fast","Hallo, dies ist ein Test. Die Sprachausgabe läuft vollständig offline.")]
    for lang,name,lvl,text in cases:
        v=[x for x in c.VOICES[lang] if x.get("name")==name][0]
        req=c.build_request(v,text,1.0,f"/tmp/out_{lang}.wav")
        prog=[]; t=time.time()
        path,secs=s.run(req,lvl,lambda i,n:prog.append((i,n)))
        print(f"{lang}/{name}/{lvl}: {secs:.1f}s audio in {time.time()-t:.1f}s, chunks {prog[-1]}, worker RSS {worker_rss(s):.0f} MB")
    # cancel test
    long="This is a long sentence that repeats itself. "*40
    v=c.VOICES["en"][0]; req=c.build_request(v,long,1.0,"/tmp/out_cancel.wav")
    threading.Timer(3,s.cancel).start()
    try: s.run(req,"light")
    except c.Cancelled: print("cancel: OK (Cancelled raised, worker alive:",s.running(),")")
    # recovery after cancel
    req=c.build_request(v,"Back again.",1.0,"/tmp/out_after.wav"); print("after cancel:",s.run(req,"balanced")[1],"s")
    s.stop(); print("stopped, running:",s.running())
