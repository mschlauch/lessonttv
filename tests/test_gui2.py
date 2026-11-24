import sys, types, time, os, json, tempfile
sys.path.insert(0,"/home/claude/localtts")
class W:
    def __init__(s,*a,**k): s._cur=0; s._vals=list(k.get("values",[])); s._txt=""; s._cfg={}
    def __getattr__(s,n): return lambda *a,**k: None
    def current(s,i=None):
        if i is None: return s._cur
        s._cur=i
    def get(s): return s._vals[s._cur] if s._vals else ""
    def config(s,**k):
        s._cfg.update(k)
        if "values" in k: s._vals=list(k["values"])
    configure=config
class Text(W):
    def insert(s,i,t): s._txt=t
    def get(s,a,b=None): return s._txt+"\n"
    def delete(s,*a): s._txt=""
class Entry(W):
    def get(s): return s._txt
    def delete(s,*a): s._txt=""
    def insert(s,i,t): s._txt=t
class Tree(W):
    def __init__(s,*a,**k): super().__init__(*a,**k); s.rows=[]; s.sel=[]
    def insert(s,p,i,values=()): s.rows.append(values); return f"I{len(s.rows)-1}"
    def get_children(s): return [f"I{i}" for i in range(len(s.rows))]
    def delete(s,iid): s.rows=[]            # GUI clears all rows then re-inserts
    def selection(s): return s.sel
    def index(s,iid): return int(iid[1:])
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
for n in ("Frame","Label","Combobox","Scale","Radiobutton","Button","Progressbar","LabelFrame","Scrollbar"): setattr(ttk,n,W)
ttk.Entry=Entry; ttk.Treeview=Tree
fd=types.ModuleType("tkinter.filedialog"); mb=types.ModuleType("tkinter.messagebox")
msgs=[]; mb.showerror=lambda t,m: msgs.append(("error",m)); mb.showwarning=lambda t,m: msgs.append(("warn",m)); mb.showinfo=lambda t,m: msgs.append(("info",m)); mb.askyesno=lambda *a: True
tk.ttk=ttk; tk.filedialog=fd; tk.messagebox=mb
sys.modules.update({"tkinter":tk,"tkinter.ttk":ttk,"tkinter.filedialog":fd,"tkinter.messagebox":mb})
if __name__=="__main__":
    import multiprocessing as mp; mp.freeze_support()
    import localtts_gui as g, localtts_core as c
    os.environ["LOCALTTS_EDGE_MOCK"]="/tmp/edge_mock.mp3"
    a=g.App()
    def pick(lang_code, voice_name, text, name=""):
        a.lang_cb.current([x for x,_ in c.LANGUAGES].index(lang_code)); a._on_lang()
        a.voice_cb.current([v.get("name") for v in a.voices].index(voice_name)); a._update_voice_note()
        a.text.insert("1.0",text); a.name_entry.insert(0,name); a._batch_add()
    pick("en","af_heart","Hello from the batch. First item.")
    pick("de","eva_k","Hallo, zweiter Eintrag im Stapel.")
    print("edge note:", a.voice_note._cfg["text"][:60] if False else "")
    pick("en","en-US-AriaNeural","Third item uses the Edge preview voice.","edge preview")
    print("edge voice note ->",a.voice_note._cfg["text"][:70])
    pick("de","eva_k","Dieser Eintrag wird wieder entfernt.")
    print("rows:",[(r[0],r[1],r[2]) for r in a.tree.rows])
    a.tree.sel=["I3"]; a._batch_remove(); print("after remove:",len(a.batch),"rows; status:",a.status._cfg["text"])
    print("persisted batch.json items:",len(json.load(open(g.BATCH_FILE))))
    a2=g.App(); print("reloaded in new app instance:",len(a2.batch),"items")
    out=tempfile.mkdtemp(prefix="gui_batch_"); fd.askdirectory=lambda **k: out
    a.fmt_cb.current(1)   # MP3
    a._batch_process(); t=time.time()
    while a.busy and time.time()-t<90: time.sleep(0.2)
    print("busy:",a.busy,"| files:",sorted(os.listdir(out)),"| status:",a.status._cfg["text"][:60],"| dialogs:",msgs)
    # cancel
    a.batch=[dict(lang="en",voice="k:af_heart",speed=1.0,text="Long repeating sentence. "*40,name="") for _ in range(3)]
    out2=tempfile.mkdtemp(prefix="gui_batch_"); fd.askdirectory=lambda **k: out2; a.fmt_cb.current(0)
    a._batch_process(); time.sleep(2.5); a._stop(); t=time.time()
    while a.busy and time.time()-t<30: time.sleep(0.2)
    print("cancelled batch -> busy:",a.busy,"| status:",a.status._cfg["text"][:50],"| files:",os.listdir(out2))
    a._quit()
