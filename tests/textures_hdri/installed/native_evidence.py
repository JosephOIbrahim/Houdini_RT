import re

def native_events(path):
    pattern=re.compile(r'\[debug\] (pause request|pause ack|resume|request|present|batch) renderer=')
    sessions={}; count=0; events=[]; latest={}; stale=[]
    for number,line in enumerate(path.read_text(errors='replace').splitlines(),1):
        if not line.startswith('HoudiniRT:'): continue
        created=re.search(r'render session=(\S+) created',line)
        if created: count+=1;sessions[created[1].lower()]=count
        match=pattern.search(line)
        if not match: continue
        item={'kind':match[1].replace(' ','_'),'line':number,'sequence':len(events)}
        for key,value in re.findall(r'(\w+)=([^\s]+)',line):
            if key=='renderer': item[key]=value.lower();continue
            try:item[key]=float(value) if '.' in value else int(value)
            except ValueError:item[key]=value
        item['session']=sessions.get(item['renderer']); identity=(item['renderer'],item['session'])
        if item['kind']=='request':latest[identity]=item
        if item['kind']=='present':
            request=latest.get(identity)
            if not request or (request['generation'],request['scene'])!=(item['generation'],item['scene']):stale.append(item)
        events.append(item)
    assert not stale,('Stale/missing-request publication',stale)
    return events


def workflow(result,log,limits,require):
    events=native_events(log);observed=result['native_work_events']
    require(len(events)==len(observed),'Native event census mismatch')
    for native,saved in zip(events,observed):
        require(all(saved.get(k)==v for k,v in native.items() if k!='line'),'Native event differs from raw log')
    def selected(record,kind):
        found=events[record['sequence']] if 'sequence' in record and 0<=record['sequence']<len(events) else next((e for e in events if e['line']==record.get('line')),None)
        assert found and found['kind']==kind,('Missing selected native event',kind,record)
        require(all(found[k]==v for k,v in record.items() if k in found),'Selected native event field mismatch')
        require(all(k in record and record[k]==found[k] for k in ('renderer','generation')),'Missing native identity')
        return found
    pause=result['active_pause'];ack=selected(pause['ack'],'pause_ack');identity=(ack['renderer'],ack['session'])
    require(0<ack['samples']<65536 and pause['confirmed_hold_seconds']>=limits['paused_seconds_min'],'No incomplete-render pause hold')
    resume=next((e for e in events if e['sequence']>ack['sequence'] and (e.get('renderer'),e.get('session'))==identity and e['kind']=='resume'),None)
    assert resume,'No resume after ACK'
    require(resume['time_s']-ack['time_s']>=limits['paused_seconds_min'],'Raw pause interval too short')
    require(not any(e['kind'] in ('batch','present') and (e.get('renderer'),e.get('session'))==identity for e in events[ack['sequence']+1:resume['sequence']]),'GPU work during acknowledged pause')
    edit=result['paused_material_edit'];latest=selected(edit['resumed_latest'],'present');before=selected(edit['edit']['before_request'],'request')
    require(latest['sequence']>resume['sequence'] and (latest['renderer'],latest['session'])==identity,'Edited image precedes matching resume')
    require(latest['generation']>before['generation'] and latest['scene']!=before['scene'] and latest['samples']>0,'Paused material did not publish changed content')
    require(edit['no_work_hold_confirmed_ns']-edit['edit']['driver_returned_ns']>=2_000_000_000,'Paused material hold shorter than2seconds')
    for edit in result['material_publications']:
        before=selected(edit['before_request'],'request');present=selected(edit['presentation'],'present')
        require((before['renderer'],before['session'])==(present['renderer'],present['session']),'Material edit mixed sessions')
        require(present['generation']>before['generation'] and present['scene']!=before['scene'],'Material image did not change scene/generation')
    for name,capture in result['images'].items():
        chosen=[selected(capture[k],kind) for k,kind in (('request_before','request'),('request_after','request'),('present_before','present'),('present_after','present'))]
        require(len({(e['renderer'],e['session'],e['generation'],e['scene']) for e in chosen})==1,name+': capture changed content/session')
        require(chosen[-1]['sequence']>=chosen[-2]['sequence'] and chosen[-1]['sequence']>chosen[1]['sequence'],name+': old capture publication')
        require(chosen[-1]['samples']==limits['sample_budget'],name+': capture not fully rendered')
    reports=result['support_reports'];require(len(reports)>=3 and result.get('support_stale_after_native_material_edit'),'Support refresh/staleness missing')
    for report in reports:
        require('Not assessed' in report['text'] and 'not checked' in report['text'],'Asset assessment scope overstated')
        require('/materials/openpbr/base_image.inputs:file' in report['text'] and '/Lights/environment' in report['text'],'Native image/dome paths missing')
        require(report['asset_checks']=='Not assessed; no UI file resolution or decoding','Report asset scope mismatch')
    restored=[r for r in result['records'] if r['step']=='restored'];assert restored,'No restored state'
    last=restored[-1]
    require(last['native_renderer']==result['previous_renderer'] and last['native_paused']==result['previous_paused'],'Previous renderer/pause not restored')
    require(last['other_viewer']['renderer']=='Karma CPU' and not last['other_viewer']['paused'],'Other viewer changed')
    final=result['guide_observations'][-1]
    require(final['guides']==result['original_guides'] and final['viewports']==result['startup_viewports'],'Original grid controls not restored')
    return events,ack

