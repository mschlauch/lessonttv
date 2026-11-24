import sys, os, time, threading, tempfile
sys.path.insert(0,"/home/claude/localtts")
import localtts_core as c
if __name__=="__main__":
    import multiprocessing as mp; mp.freeze_support()
    import soundfile as sf
    d,sr=sf.read("/tmp/out_de.wav",dtype="float32"); sf.write("/tmp/edge_mock.mp3",d,sr,format="MP3")
    os.environ["LOCALTTS_EDGE_MOCK"]="/tmp/edge_mock.mp3"
    mk=lambda lang,vid,text,name="",speed=1.0: dict(lang=lang,voice=vid,text=text,speed=speed,name=name)
    items=[mk("en","k:af_heart","Hello from the first item. It uses Kokoro."),
           mk("de","p:de_DE-eva_k-x_low","Hallo, das ist der zweite Eintrag."),
           mk("en","e:en-US-AriaNeural","This one uses the Edge preview voice.",speed=1.2),
           mk("sv","p:sv_SE-lisa-medium","Hej, den här rösten är inte nedladdad."),     # must fail (not installed, no network)
           mk("de","p:de_DE-eva_k-x_low","Noch ein deutscher Satz.",name="Mein Titel"),
           mk("de","p:de_DE-eva_k-x_low","Noch ein deutscher Satz.",name="Mein Titel")]  # duplicate name
    for ext in (".wav",".mp3"):
        out=tempfile.mkdtemp(prefix="batch_"); s=c.Synth(); log=[]
        t=time.time()
        res,canc=c.run_batch(items,out,s,"balanced",ext,on_progress=lambda n,N,name,f,l: log.append((n,name,l)))
        s.stop()
        print(f"\n[{ext}] {time.time()-t:.0f}s, cancelled={canc}")
        for i,ok,name,info in res: print("  ",i+1,"OK " if ok else "FAIL",name,(f"{info:.1f}s" if ok else info[:70]))
        print("   files:",sorted(os.listdir(out)))
        voices_order=[x[1] for x in log if x[2]=="Preparing..."]; print("   processing order:",voices_order)
    # cancel in the middle
    out=tempfile.mkdtemp(prefix="batch_"); s=c.Synth(); stop=[False]
    threading.Timer(4,lambda:(stop.__setitem__(0,True),s.cancel())).start()
    long=[mk("en","k:af_heart","A long sentence that repeats. "*30) for _ in range(3)]
    res,canc=c.run_batch(long,out,s,"light",".wav",should_stop=lambda:stop[0]); s.stop()
    print("\ncancel test: cancelled =",canc,"| finished items:",len(res),"| files:",os.listdir(out))
