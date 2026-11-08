import io,contextlib,random,pandas as pd,numpy as np
from sklearn.metrics import roc_auc_score
import importlib.util
spec=importlib.util.spec_from_file_location('m','dncr_grant_scoring.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
nov=m.load_csv('data/grants-search-202611071427.csv')
aug=pd.read_csv('data/grants_snapshot_2026-08-18.csv',low_memory=False)
raw=pd.read_csv('data/grants-search-202611071427.csv',low_memory=False)
def real(df,pat):
    r=df[df.opportunity_title.str.contains(pat,case=False,na=False,regex=False)].iloc[0]
    return r.opportunity_title,r.agency_name,m._clean_html(r.summary_description),pd.to_numeric(r.expected_number_of_awards,errors='coerce'),r.award_ceiling
NPS="National Park Service"
W=[ # label, evidence, (title, agency, synopsis, n_awards, ceiling) , state_only, source
 ("Battlefield Land Acquisition","DNCR 2019 $166,360; 2022 $71,853 and $50,916",real(aug,'Battlefield Land Acquisition'),False,"real listing"),
 ("NEA Partnership Agreement","NC Arts Council, every year",real(aug,'Partnership Agreement Grants'),False,"real listing"),
 ("IMLS Museums for America","Aquarium Pine Knoll Shores $240,808, Town Creek $147,439 (2022); Museum of Natural Sciences $298,007 (FY2026)",real(raw,'Museums for America'),False,"real listing"),
 ("IMLS African American History & Culture","NC African American Heritage Commission $100,386 (2021)",real(raw,'Museum Grants for African American History'),False,"real listing"),
 ("NHPRC State Board Programming","DNCR 2020, 2022, 2024; $141,264 (2025)",("State Board Programming Grants for State Historical Records Advisory Boards","National Historical Publications and Records Commission",
   "Supports State Historical Records Advisory Boards to strengthen the statewide archival network: training for archivists and caretakers of historical records, preservation of and public access to historical records and collections, digitization, regrants to local archives and historical societies, and America 250 programming.",np.nan,np.nan),True,"program description"),
 ("NHPRC Access to Historical Records","State Archives $83,636 (2019, early legal records)",("Access to Historical Records: Archival Projects","National Historical Publications and Records Commission",
   "Supports projects that significantly improve public discovery and use of major historical records collections: processing and describing archival collections, creating finding aids, and digitizing and publishing historical records online for free public access.",np.nan,np.nan),False,"program description"),
 ("NHPRC Electronic Records","DNCR $199,999 (2015, email preservation tools)",("Electronic Records Projects","National Historical Publications and Records Commission",
   "Supports archives in creating tools, methods and collaborative projects to preserve and provide access to born-digital and electronic records of enduring historical value, including email and government electronic records.",np.nan,np.nan),False,"program description"),
 ("USDA FS Good Neighbor Agreement","DNCR $10,196,600 (2025)",("Good Neighbor Agreement - Hurricane Recovery","Forest Service",
   "Cooperative agreement with a state to assess hurricane impacts on biological and ecological resources of national forests and adjacent lands, including rare species populations, natural heritage areas and special interest areas, and to recommend restoration and management for long-term resilience.",np.nan,np.nan),True,"program description (NC names removed)"),
 ("HPF grants to SHPOs","SHPO $1.29M (FY2024)",("Historic Preservation Fund Grants to State Historic Preservation Offices",NPS,
   "Annual Historic Preservation Fund grants to State Historic Preservation Offices for statewide historic preservation programs: survey and inventory of historic properties, National Register of Historic Places nominations, preservation planning, review of federal undertakings, technical assistance, public education and subgrants to Certified Local Governments.",np.nan,np.nan),True,"program description"),
 ("Emergency Supplemental HPF","SHPO $9.17M (Florence/Michael, 2021)",("Emergency Supplemental Historic Preservation Fund Disaster Recovery Grants",NPS,
   "Emergency supplemental funding from the Historic Preservation Fund to support the preservation, stabilization, rehabilitation, repair and reconstruction of historic properties listed in or eligible for the National Register of Historic Places damaged by hurricanes and other declared disasters, plus compliance activities and surveys of historic and cultural resources.",np.nan,np.nan),True,"program description"),
 ("NPS African American Civil Rights","SHPO $50,000 (2020); Charlotte Hawkins Brown $555,334 (2023); Transportation Museum $287,442 (2019)",("Historic Preservation Fund - African American Civil Rights Grant Program",NPS,
   "Competitive grants to document, interpret, and preserve sites and stories related to the African American struggle to gain equal rights as citizens, including survey and documentation, National Register nominations, oral histories, interpretation and education, and physical preservation (rehabilitation and repair) of historic sites.",np.nan,np.nan),False,"program description"),
 ("NPS History of Equal Rights","Charlotte Hawkins Brown $278,763 (2021)",("Historic Preservation Fund - History of Equal Rights Grant Program",NPS,
   "Competitive grants for the preservation of sites related to the struggle of any and all people to achieve equal rights in America; funds physical preservation projects, pre-preservation planning and architectural plans for historic properties listed in or eligible for the National Register of Historic Places.",np.nan,np.nan),False,"program description"),
 ("NPS Semiquincentennial Grants","Historic Edenton $750,000 (2024); House in the Horseshoe $444,926 (2023); Brunswick Town $500,000 (2022)",("Historic Preservation Fund - Semiquincentennial Grant Program",NPS,
   "Competitive grants to states, tribes, local governments and nonprofits to commemorate the 250th anniversary of the founding of the United States by restoring and preserving sites and structures listed on the National Register of Historic Places that commemorate the founding of the nation.",np.nan,np.nan),False,"program description"),
 ("NPS ABPP Battlefield Restoration","Bentonville Battlefield $100,000 (2024)",("American Battlefield Protection Program - Battlefield Restoration Grants",NPS,
   "Grants to restore Revolutionary War, War of 1812 and Civil War battlefield land to its historic condition at the time of the battle, including cultural landscape reports, removal of non-historic features, replanting of historic vegetation, and interpretation of restored battlefields.",np.nan,np.nan),False,"program description"),
 ("IMLS LSTA Grants to States","State Library $5.12M (FY2025)",("Grants to States","Institute of Museum and Library Services",
   "Population-based formula grants to State Library Administrative Agencies to support library services statewide: access to information and digital resources, digitization of historical collections, literacy and lifelong learning, workforce and digital skills, and competitive subgrants to local public and academic libraries.",np.nan,np.nan),True,"program description"),
 ("LWCF State and Local Assistance","State Parks $7.89M (FY2024)",("Land and Water Conservation Fund State and Local Assistance Program",NPS,
   "The Land and Water Conservation Fund supports locally-led outdoor recreation projects through matching grants to state and local governments to acquire land and create and improve parks and recreation areas; funding is distributed annually to develop and support basic recreation facilities in every state.",np.nan,np.nan),True,"program description"),
 ("FHWA Recreational Trails Program","State Parks runs NC Trails grants each year",("Recreational Trails Program","Federal Highway Administration",
   "The Recreational Trails Program provides funds to the States to develop and maintain recreational trails and trail-related facilities for both nonmotorized and motorized recreational trail uses, including hiking, bicycling, equestrian use and other trail activities.",np.nan,np.nan),True,"program description"),
 ("NOAA coastal resilience (historic site shoreline)","Brunswick Town/Fort Anderson $2.5M (2025)",("Coastal Habitat Restoration and Resilience Grants","DOC NOAA",
   "Competitive funding for habitat restoration and coastal resilience projects that use natural infrastructure such as living shorelines and marsh restoration to reduce erosion and flooding risk, protect coastal communities and cultural resources, and restore coastal ecosystems.",np.nan,np.nan),False,"program description"),
 ("NFWF National Coastal Resilience Fund","Brunswick Town/Fort Anderson $2,002,500 (2020)",("National Coastal Resilience Fund","National Fish and Wildlife Foundation (NOAA-funded)",
   "Supports nature-based solutions that restore, increase and strengthen natural infrastructure such as marshes, dunes and living shorelines to protect coastal communities from storms, flooding and sea-level rise while improving habitat for fish and wildlife.",np.nan,np.nan),False,"program description"),
 ("NEH public programs (Created Equal)","NC Museum of History (2013)",("Public Humanities Programs: Created Equal Film Series","National Endowment for the Humanities",
   "Supports museums, libraries, historic sites and other public institutions to host public film screenings and humanities discussions on the history of civil rights in America, with scholars leading community conversations and public programs.",np.nan,np.nan),False,"program description"),
]
open_dl=nov.deadline.dropna(); open_dl=open_dl[open_dl.dt.date>=m.AS_OF_DATE]
NEUTRAL=open_dl.median().normalize()
def kn_df():
    rows=[]
    for i,(l,e,(t,a,s,n,c),so,src) in enumerate(W):
        rows.append(dict(opp=f"WIN-{i+1:02d}",title=t,agency=a,deadline=NEUTRAL,elig='test',elig_reason='state government is eligible',
                         synopsis=s[:m.SYNOPSIS_CHARS],cfda='',url='',state_only=so,n_awards=n,label=l,evidence=e,src=src))
    k=pd.DataFrame(rows); k['match_text']=k.title+'. '+k.synopsis; return k
def score(allx):
    g=allx.apply(m.gates,axis=1,result_type='expand'); allx['gate_pass'],allx['gate_reason'],allx['forecast']=g[0],g[1],g[2]
    pm,cal=m.plan_match(allx); allx=pd.concat([allx.reset_index(drop=True),pm],axis=1)
    rows=[]
    for _,r in allx.iterrows():
        r=r.to_dict(); r['U'],_=m.urgency(r['obj'],r['title']); r['route'],r['dncr']=m.route(r['agency'],r['title'],r['obj'])
        r['anchor'],_=m.pick_anchor(r['title']); r.update(m.simulate_hours(r['anchor']))
        r['P'],r['P_case'],r['P_source']=m.win_probability(r); r['M']=1.0; r['final']=r['A']
        rows.append(r)
    d=pd.DataFrame(rows); b=d.apply(m.decide,axis=1,result_type='expand'); d['button']=b[0]; return d
if __name__=='__main__':
    kn=kn_df()
    d=score(pd.concat([nov,kn.drop(columns=['label','evidence','src'])],ignore_index=True))
    pool=d[d.gate_pass & ~d.opp.str.startswith('WIN')]
    print('neutral deadline',NEUTRAL.date(),'| pool',len(pool))
    out=[]
    for _,k in kn.iterrows():
        r=d[d.opp==k.opp].iloc[0]
        out.append(dict(Program=k.label,DNCR_won=k.evidence,Text=k.src,Final=round(r.final,1),A=round(r.A,1),M=round(r.M,2),Chance_case=r.P_case,
                        Rank=int((pool.final>r.final).sum())+1,Top_pct=round(100*(pool.final>=r.final).mean(),1),Button=r.button,Who=r.route))
    o=pd.DataFrame(out).sort_values('Final',ascending=False)
    print(o[['Program','Final','A','M','Rank','Top_pct','Button','Text']].to_string(index=False))
    print('Chase',(o.Button=='Chase').sum(),'| You decide',(o.Button=='You decide').sum(),'| Skip',(o.Button=='Skip').sum(),'| Route',(o.Button=='Route').sum(),
          '| median rank',o.Rank.median(),'| in top 50:',(o.Rank<=50).sum())
    o.to_excel('outputs/DNCR_20_wins_test.xlsx',index=False)
