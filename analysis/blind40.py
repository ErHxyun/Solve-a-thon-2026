import random,pandas as pd,numpy as np
from sklearn.metrics import roc_auc_score
from wins20 import m,nov,raw,kn_df,score,NEUTRAL
pats=['Role of Work in Health Disparities','National Fish Passage','RESTORE Act Centers of Excellence','Landscape Scale Restoration Grant Program for Non-Federal',
      'Fiscal Year 2026 Flood Mitigation Assistance Swift','EDA FY25 Disaster','NSF STEM K-12','Coverdell','DARRP Restoration','Section 811',
      'Great Plains Cooperative Ecosystem','National Parks Business Plan Internship','Super Intelligence','Native Entities Grant Program','Tribal Broadband Connectivity',
      'Emmett Till','Lifespan Respite','Counter Unmanned','Animal Food Regulatory','Highest State Courts']
kn=kn_df()
dec=[]
for p in pats:
    rr=raw[raw.opportunity_title.str.contains(p,na=False,regex=False)].iloc[0]; dec.append(rr.opportunity_number)
nov2=nov.copy(); nov2.loc[nov2.opp.isin(dec),'deadline']=NEUTRAL      # same deadline for every test item
d=score(pd.concat([nov2,kn.drop(columns=['label','evidence','src'])],ignore_index=True))
items=[]
for _,k in kn.iterrows():
    r=d[d.opp==k.opp].iloc[0]
    items.append(dict(label=1,name=k.label,title=k.title,agency=k.agency,syn=k.synopsis,app='state governments' if k.state_only else 'see description',
                      award='not stated',our=r.final if r.gate_pass else 0.0,btn=r.button,evidence=k.evidence))
for o in dec:
    rr=raw[raw.opportunity_number==o].iloc[0]; r=d[d.opp==o].iloc[0]
    aw=[]
    if pd.notna(rr.award_ceiling): aw.append(f"ceiling ${rr.award_ceiling:,.0f}")
    if pd.notna(rr.expected_number_of_awards): aw.append(f"{rr.expected_number_of_awards:.0f} expected awards")
    items.append(dict(label=0,name=rr.opportunity_title,title=rr.opportunity_title,agency=rr.agency_name,syn=m._clean_html(rr.summary_description)[:1500],
                      app=str(rr.applicant_types).replace('_',' ').replace(';','; '),award='; '.join(aw) or 'not stated',
                      our=r.final if r.gate_pass else 0.0,btn=r.button,evidence='decoy: not a DNCR program (another agency or sector is the natural applicant); no DNCR award found'))
random.Random(11).shuffle(items)
for i,x in enumerate(items): x['id']=f"G{i+1:02d}"
k=pd.DataFrame(items); k['our_rank']=k.our.rank(ascending=False,method='min').astype(int)
print('our AUC %.3f | wins in our top 20: %d/20 | wins Chase/YouDecide: %d'%(roc_auc_score(k.label,k.our),k.nsmallest(20,'our_rank').label.sum(),
      ((k.label==1)&k.btn.isin(['Chase','You decide'])).sum()))
print('decoys not Skip:',k[(k.label==0)&(k.btn!='Skip')][['name','our','btn']].to_string(index=False))
k.rename(columns={'our':'our_final','btn':'our_button'})[['id','label','name','agency','evidence','our_final','our_rank','our_button']].sort_values('id')\
  .to_excel('outputs/blind_test_answer_key.xlsx',index=False)
head=open('blind_prompt_head.md').read().replace('Below are 20 opportunities','Below are 40 opportunities').replace('score 20 federal','score 40 federal')\
  .replace("Today is 2026-11-07. If a deadline is not posted, score U = 3.",f"Today is 2026-11-07. To keep timing neutral, every item has the same deadline ({NEUTRAL.date()}); score U the same way for all of them. Estimate workload hours yourself from the description.")
body="\n".join(f"### {x['id']}. {x['title']}\n- Agency: {x['agency']}\n- Eligible applicants: {x['app']}\n- Deadline: {NEUTRAL.date()}\n- Award: {x['award']}\n- Description: {x['syn']}\n" for x in items)
open('outputs/blind_test_prompt.md','w').write(head+"\n## The 40 opportunities\n\n"+body)
