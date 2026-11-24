import sys; sys.path.insert(0,"/home/claude/localtts")
import localtts_core as c
if __name__=="__main__":
    import multiprocessing as mp; mp.freeze_support()
    v=c.VOICES["lt"][0]; s=c.Synth()
    req=c.build_request(v,"Labas rytas, kaip sekasi? Šiandien graži diena.",1.0,"/tmp/out_lt.wav")
    path,secs=s.run(req,"balanced"); print(f"pipeline OK: {secs:.1f}s audio (stand-in model; sound not meaningful)"); s.stop()
