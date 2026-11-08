import importlib.util, re, numpy as np, pandas as pd
spec=importlib.util.spec_from_file_location('m','dncr_grant_scoring.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
nov=m.load_csv('data/grants-search-202611071427.csv')
aug=pd.read_csv('data/grants_snapshot_2026-08-18.csv',low_memory=False)
raw=pd.read_csv('data/grants-search-202611071427.csv',low_memory=False)
def real(df,pat):
    r=df[df.opportunity_title.str.contains(pat,case=False,na=False)].iloc[0]
    return r.opportunity_title, r.agency_name, m._clean_html(r.summary_description)
K=[ # (label, DNCR evidence, title, agency, synopsis, source of text)
 ("Battlefield Land Acquisition","DNCR won 2019, Jan 2022, Aug 2022 (NPS)",*real(aug,'Battlefield Land Acquisition'),"Grants.gov listing (Aug 2026)"),
 ("NEA Partnership Agreement","NC Arts Council (DNCR) receives it every year",*real(aug,'Partnership Agreement Grants'),"Grants.gov listing (Aug 2026)"),
 ("IMLS Museums for America","NC Aquarium Pine Knoll Shores $240,808 and Town Creek $147,439 (2022); NC Museum of Natural Sciences $114,187 (2013)",*real(raw,'Museums for America'),"Grants.gov listing (Nov 2026)"),
 ("NHPRC State Board Programming","DNCR 2020, 2022, 2024; $141,264 oral history project 2025 (NHPRC)","State Board Programming Grants for State Historical Records Advisory Boards","National Historical Publications and Records Commission",
  "Supports State Historical Records Advisory Boards to strengthen the statewide archival network: training for archivists and caretakers of historical records, preservation of and public access to historical records and collections, digitization, regrants to local archives and historical societies, and America 250 programming.","program description (NHPRC)"),
 ("USDA FS Good Neighbor Agreement (Helene)","DNCR $10,196,600, 2025 (OSBM report)","FY 2025 Good Neighbor Agreement Hurricane Helene Recovery","Forest Service",
  "Cooperative agreement with the state to assess Hurricane Helene impacts on biological and ecological resources of the Pisgah and Nantahala National Forests and adjacent lands, including rare species populations, natural heritage areas and Special Interest Areas, and to recommend restoration and management for long-term resilience.","OSBM award report"),
 ("HPF grants to SHPOs","NC SHPO (DNCR) $1.29M FY2024 (NPS)","Historic Preservation Fund Grants to State Historic Preservation Offices","National Park Service",
  "Annual Historic Preservation Fund grants to State Historic Preservation Offices for statewide historic preservation programs: survey and inventory of historic properties, National Register of Historic Places nominations, preservation planning, review of federal undertakings, technical assistance, public education and subgrants to Certified Local Governments.","program description (NPS NOFO)"),
 ("IMLS LSTA Grants to States","State Library (DNCR) $5.12M FY2025 (IMLS)","Grants to States","Institute of Museum and Library Services",
  "Population-based formula grants to State Library Administrative Agencies to support library services statewide: access to information and digital resources, digitization of historical collections, literacy and lifelong learning, workforce and digital skills, and competitive subgrants to local public and academic libraries.","program description (IMLS)"),
 ("LWCF State and Local Assistance","State Parks (DNCR) $7.89M FY2024 (NPS)","Land and Water Conservation Fund State and Local Assistance Program","National Park Service",
  "The Land and Water Conservation Fund supports locally-led outdoor recreation projects through matching grants to state and local governments to acquire land and create and improve parks and recreation areas; funding is distributed annually to develop and support basic recreation facilities in every state.","NPS program page"),
 ("FHWA Recreational Trails Program","State Parks (DNCR) runs NC Trails grants each year (DNCR)","Recreational Trails Program","Federal Highway Administration",
  "The Recreational Trails Program provides funds to the States to develop and maintain recreational trails and trail-related facilities for both nonmotorized and motorized recreational trail uses, including hiking, bicycling, equestrian use and other trail activities.","FHWA program page"),
 ("Emergency Supplemental HPF (disaster)","SHPO (DNCR) prime recipient, $9.17M Florence/Michael subgrants (2021)","Emergency Supplemental Historic Preservation Fund Disaster Recovery Grants","National Park Service",
  "Emergency supplemental funding from the Historic Preservation Fund to support the preservation, stabilization, rehabilitation, repair and reconstruction of historic properties listed in or eligible for the National Register of Historic Places damaged by hurricanes and other declared disasters, plus compliance activities and surveys of historic and cultural resources.","NPS FY2025 program page"),
]
kn=pd.DataFrame([dict(opp=f"KNOWN-{i+1}",title=t,agency=a,deadline=pd.Timestamp('2027-03-31'),elig='test',elig_reason='state government is eligible',
                       synopsis=s[:m.SYNOPSIS_CHARS],cfda='',url='',label=l,evidence=e,src=src) for i,(l,e,t,a,s,src) in enumerate(K)])
so={1:(False,None),2:(False,62),3:(False,122),4:(True,None),5:(True,None),6:(True,None),7:(True,None),8:(True,None),9:(True,None),10:(True,None)}
kn['state_only']=[so[i+1][0] for i in range(10)]; kn['n_awards']=[so[i+1][1] for i in range(10)]
kn['match_text']=(kn.title+'. '+kn.synopsis)
allx=pd.concat([nov,kn.drop(columns=['label','evidence','src'])],ignore_index=True)
g=allx.apply(m.gates,axis=1,result_type='expand'); allx['gate_pass'],allx['gate_reason'],allx['forecast']=g[0],g[1],g[2]
pm,cal=m.plan_match(allx); allx=pd.concat([allx.reset_index(drop=True),pm],axis=1)
rows=[]
for _,r in allx.iterrows():
    r=r.to_dict(); r['U'],_=m.urgency(r['obj'],r['title']); r['route'],r['dncr']=m.route(r['agency'],r['title'],r['obj'])
    r['anchor'],_=m.pick_anchor(r['title']); r.update(m.simulate_hours(r['anchor'])); r['W']=m.workload_score(r['H']); r["P"],r["P_case"],r["P_source"]=m.win_probability(r); r["M"]=m.chance_nudge(r["P"],r["P_case"]); r["final"]=m.final_score(r["A"],r["M"])
    rows.append(r)
d=pd.DataFrame(rows); b=d.apply(m.decide,axis=1,result_type='expand'); d['button']=b[0]
pool=d[d.gate_pass & ~d.opp.str.startswith('KNOWN')]
print('calibration',cal,'| eligible real listings:',len(pool))
out=[]
for i,k in kn.iterrows():
    r=d[d.opp==k.opp].iloc[0]
    rank=int((pool.final>r.final).sum())+1
    pct=100*(pool.final<r.final).mean()
    out.append(dict(Program=k.label,DNCR_won=k.evidence,Text_from=k.src,Final=round(r.final,1),A=round(r.A,1),S=round(r.S,1),K=round(r.K,1),Objective=r.obj,P=round(r.P,2),M=round(r.M,2),P_case=r.P_case,U=r.U,Hours=round(r.H),
                    Rank=rank,Top_pct=round(100-pct,1),Button=r.button,Who_applies=r.route))
o=pd.DataFrame(out).sort_values('Final',ascending=False)
print('pool',len(pool));print(o[['Program','Final','A','S','K','M','P_case','Rank','Button']].to_string(index=False))
o.to_excel('outputs/DNCR_known_wins_test.xlsx',index=False)
print('real MFA listing in Nov data gate:', d[d.title.str.contains('Museums for America')&~d.opp.str.startswith('KNOWN')][['gate_reason']].values.tolist())
