import sys, types, time, os, threading
sys.path.insert(0,"/home/claude/localtts")
class W:
    def __init__(s,*a,**k): s._cur=0; s._vals=k.get("values",[]); s._txt=""; s._cfg={}
    def __getattr__(s,n): return lambda *a,**k: None
    def current(s,i=None):
        if i is None: return s._cur
        s._cur=i
    def config(s,**k):
        s._cfg.update(k)
        if "values" in k: s._vals=k["values"]
    configure=config
class Text(W):
    def insert(s,i,t): s._txt=t
    def get(s,a,b=None): return s._txt+"\n"
    def delete(s,*a): s._txt=""
class Var:
    def __init__(s,value=None,**k): s.v=value
    def get(s): return s.v
    def set(s,v): s.v=v
class Tk(W):
    def after(s,ms,fn=None,*a):
        if fn and ms==0: fn(*a)
        return 1
tk=types.ModuleType("tkinter"); tk.Tk=Tk; tk.Text=Text; tk.StringVar=Var; tk.DoubleVar=Var
ttk=types.ModuleType("tkinter.ttk")
for n in ("Frame","Label","Combobox","Scale","Radiobutton","Button","Progressbar","LabelFrame"): setattr(ttk,n,W)
fd=types.ModuleType("tkinter.filedialog"); mb=types.ModuleType("tkinter.messagebox")
errs=[]; mb.showerror=lambda t,m: errs.append(m)
tk.ttk=ttk; tk.filedialog=fd; tk.messagebox=mb
sys.modules.update({"tkinter":tk,"tkinter.ttk":ttk,"tkinter.filedialog":fd,"tkinter.messagebox":mb})
if __name__=="__main__":
    import localtts_gui as g, localtts_core as c
    a=g.App()
    print("startup voices:",len(a.voices),"first:",a.voices[0]["label"])
    for i,(code,_) in enumerate(c.LANGUAGES):
        a.lang_cb.current(i); a._on_lang(); print(f"  {code}: {len(a.voices)} voices, first = {a.voices[0]['label']}")
    # pick German / eva_k and speak
    a.lang_cb.current(4); a._on_lang()
    a.voice_cb.current([v["name"] for v in a.voices].index("eva_k")); a._update_voice_note()
    print("note:",a.voice_note._cfg.get("text"))
    a.text.insert("1.0","Hallo Welt. Das ist ein Test der grafischen Oberfläche."); a.level.set("light")
    played=[]; a._play=lambda p: played.append(p)
    a._start(play=True)
    t=time.time()
    while a.busy and time.time()-t<60: time.sleep(0.2)
    print("busy after:",a.busy,"| last:",bool(a.last),"| played:",bool(played),"| errors:",errs,"| status:",a.status._cfg.get("text"))
    # save reuses result
    fd.asksaveasfilename=lambda **k:"/tmp/saved_gui.wav"
    a._save(); print("saved exists:",os.path.isfile("/tmp/saved_gui.wav"), os.path.getsize("/tmp/saved_gui.wav"))
    # cancel while generating
    a.text.insert("1.0","Dies ist ein langer Satz, der sich wiederholt. "*60); a._start(play=False)
    time.sleep(1.5); a._stop(); t=time.time()
    while a.busy and time.time()-t<20: time.sleep(0.2)
    print("after stop: busy",a.busy,"| last cleared:",a.last is None,"| status:",a.status._cfg.get("text"))
    a._quit(); print("quit ok")
