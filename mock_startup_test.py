import os, sys, types, tempfile
os.environ['CS2_DATA_DIR']=tempfile.mkdtemp(prefix='cs2v45_startup_')
class Cache:
    def __call__(self,*a,**k):
        if a and callable(a[0]) and len(a)==1 and not k: return a[0]
        return lambda fn: fn
    def clear(self): pass
class Dummy:
    def __init__(self, name='dummy'): self.name=name
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def __call__(self,*a,**k): return None
    def __getattr__(self,n):
        if n in ('sidebar',): return self
        return lambda *a,**k: None
    def metric(self,*a,**k): return None
    def button(self,*a,**k): return False
    def checkbox(self,*a,**k): return k.get('value',False)
    def multiselect(self,*a,**k): return k.get('default',[])
    def slider(self,*a,**k): return k.get('value', a[3] if len(a)>3 else 0)
    def text_input(self,*a,**k): return k.get('value', a[1] if len(a)>1 else '')
    def text_area(self,*a,**k): return k.get('value','')
    def selectbox(self,*a,**k):
        opts=a[1] if len(a)>1 else k.get('options',[]); return opts[0] if opts else None
    def file_uploader(self,*a,**k): return None
    def data_editor(self,data,*a,**k): return data
    def columns(self,spec,*a,**k): return [Dummy(f'col{i}') for i in range(spec if isinstance(spec,int) else len(spec))]
    def tabs(self,names,*a,**k): return [Dummy(f'tab{i}') for i,_ in enumerate(names)]
    def spinner(self,*a,**k): return Dummy('spinner')
    def expander(self,*a,**k): return Dummy('expander')
class Fake(types.ModuleType):
    def __init__(self):
        super().__init__('streamlit'); self.cache_data=Cache(); self.secrets={}; self.session_state={
            'cs2_board':[{'status':'PASS','data_score':0,'player':'Test','team':'A','opponent':'B','probability':0,'abs_edge':0,'projection':None,'flags':[]}],
            'cs2_board_status':{},'cs2_line_source_status':{},'cs2_last_refresh_iso':'2026-07-14T00:00:00+00:00','cs2_manual_props':[]
        }; self.sidebar=Dummy('sidebar')
    def __getattr__(self,n):
        d=Dummy(n); return getattr(d,n,None) or d
    def set_page_config(self,*a,**k): pass
    def markdown(self,*a,**k): pass
    def columns(self,*a,**k): return Dummy().columns(*a,**k)
    def tabs(self,*a,**k): return Dummy().tabs(*a,**k)
    def button(self,*a,**k): return False
    def checkbox(self,*a,**k): return k.get('value',False)
    def multiselect(self,*a,**k): return k.get('default',[])
    def slider(self,*a,**k): return k.get('value', a[3] if len(a)>3 else 0)
    def text_input(self,*a,**k): return k.get('value', a[1] if len(a)>1 else '')
    def text_area(self,*a,**k): return k.get('value','')
    def selectbox(self,*a,**k):
        opts=a[1] if len(a)>1 else k.get('options',[]); return opts[0] if opts else None
    def file_uploader(self,*a,**k): return None
    def data_editor(self,data,*a,**k): return data
    def spinner(self,*a,**k): return Dummy('spinner')
    def expander(self,*a,**k): return Dummy('expander')
    def rerun(self): pass
fake=Fake(); sys.modules['streamlit']=fake
code=open(__file__.replace('mock_startup_test.py','app.py'),encoding='utf-8').read()
exec(compile(code,'app.py','exec'),{})
print('mocked full Streamlit startup passed')
