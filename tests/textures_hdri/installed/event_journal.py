"""Incremental native-event observer; observation timestamps include log polling delay."""
from pathlib import Path
import re
import time

EVENT=re.compile(r'\[debug\] (pause request|pause ack|resume|request|present|batch) renderer=')
PAIR=re.compile(r'(\w+)=([^\s]+)')
class EventJournal:
    def __init__(self,path):
        self.path=Path(path); self.offset=0; self.partial=''; self.events=[]; self.latest={}
        self.polls=[]; self.renderer=None; self.sessions={}; self.session_count=0
    def bind(self,renderer):
        self.renderer=renderer.lower()
        self.latest={}
        for event in self.events:
            if event.get('renderer')==self.renderer and event.get('session')==self.sessions.get(self.renderer): self.latest[event['kind']]=event
    def pump(self):
        now=time.perf_counter_ns(); self.polls.append(now)
        if not self.path.exists(): return
        with self.path.open('rb') as stream:
            stream.seek(self.offset); data=stream.read(); self.offset=stream.tell()
        text=self.partial+data.decode('utf-8',errors='replace')
        lines=text.split('\n'); self.partial=lines.pop()
        for line in lines:
            created=re.search(r'render session=(\S+) created',line)
            if created:
                self.session_count+=1; pointer=created[1].lower()
                self.sessions[pointer]=self.session_count
                if pointer==self.renderer:self.latest={}
            match=EVENT.search(line)
            if not line.startswith('HoudiniRT:') or not match: continue
            item={'kind':match[1].replace(' ','_'),'observed_ns':now}
            for key,value in PAIR.findall(line):
                if key=='renderer': item[key]=value.lower(); continue
                try: item[key]=float(value) if '.' in value else int(value)
                except ValueError: item[key]=value
            item['session']=self.sessions.get(item.get('renderer')); item['sequence']=len(self.events)
            self.events.append(item)
            if self.renderer is None or item.get('renderer')==self.renderer:
                self.latest[item['kind']]=item
    def current(self):
        self.pump(); request=self.latest.get('request'); present=self.latest.get('present')
        return bool(request and present and request['generation']==present['generation']
                    and request['scene']==present['scene'])
