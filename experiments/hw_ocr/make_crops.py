"""Cut test crops (boxes picked by eye in debug-PNG display coords), write truth.json skeleton."""
import json, sys
import numpy as np, pymupdf, cv2
ROOT = "../../"
# (part, page, field, kind, box x0,y0,x1,y1 in 2000px-max-side display coords, ai spec)
HD, PD, HH, PH = "handwritten_digits", "printed_digits", "hebrew_handwritten", "hebrew_printed"
S = [
 (1,2,"central_number",HD,(195,360,330,430),"central"), (1,2,"received_date",HD,(185,430,330,495),"date"),
 (1,2,"national_id",HD,(980,20,1110,65),"id"), (1,2,"name",HH,(1110,5,1370,65),None),
 (1,2,"commitment_number",PD,(485,865,620,910),"commit0"), (1,2,"visit_date_printed",PD,(1035,283,1195,327),None),
 (1,3,"central_number",HD,(90,425,355,560),"central"), (1,3,"national_id",HD,(1205,30,1400,105),"id"),
 (1,3,"name",HH,(1365,35,1655,95),None), (1,3,"commitment_number",PD,(435,1045,645,1100),"commit0"),
 (1,3,"sender",PH,(195,90,305,135),None),
 (1,4,"central_number",HD,(210,325,370,395),"central"), (1,4,"received_date",HD,(210,390,345,475),"date"),
 (1,4,"national_id",HD,(730,10,995,65),"id"), (1,4,"name",HH,(1025,10,1290,60),None),
 (1,4,"commitment_number",PD,(460,820,630,860),"commit0"),
 (1,7,"central_number",HD,(200,335,420,425),"central"), (1,7,"received_date",HD,(275,420,425,505),"date"),
 (1,7,"national_id",HD,(1040,20,1175,75),"id"), (1,7,"name",HH,(1160,20,1385,70),None),
 (1,19,"central_number",HD,(210,595,400,705),"central"), (1,19,"received_date",HD,(295,665,485,795),"date"),
 (1,19,"national_id",HD,(880,590,1050,645),"id"), (1,19,"commitment_number",PD,(875,710,1010,750),"commit0"),
 (1,19,"sender",PH,(1005,125,1165,180),None), (1,19,"sender_long",PH,(770,175,1140,225),None),
 (1,25,"central_number",HD,(310,320,625,510),"central"), (1,25,"received_date",HD,(465,305,635,420),"date"),
 (1,25,"national_id",HD,(1130,25,1280,75),"id"), (1,25,"commitment_number",PD,(410,1080,620,1125),"commit0"),
 (1,14,"commitment_number",HD,(495,915,775,975),"commit0"), (1,14,"received_date",PD,(765,540,1045,605),"date"),
 (1,14,"free_text",HH,(905,695,1335,770),None),
 (1,21,"received_date",HD,(135,255,355,315),None), (1,21,"national_id",HD,(250,560,440,610),"id"),
 (1,21,"central_number",PD,(1265,245,1375,290),"central"), (1,21,"free_text",HH,(185,345,645,425),None),
 (2,3,"commitment_number",PD,(685,710,830,750),"commit0"), (2,3,"received_date",HD,(265,435,440,495),"date"),
 (2,3,"national_id",HD,(605,400,750,445),"id"), (2,3,"central_number",PD,(800,135,910,180),"central"),
 (2,3,"free_text",HH,(260,515,505,570),None), (2,3,"sender",PH,(145,220,295,260),None),
 (2,10,"commitment_number",HD,(40,950,365,1015),"commit0"), (2,10,"national_id",HD,(35,680,235,730),"id"),
 (2,10,"central_number",PD,(575,1055,690,1100),"central"), (2,10,"name",HH,(555,660,775,730),None),
 (2,10,"sender",PH,(845,400,1005,455),None),
]
ai = {}
for p in (1, 2):
    for v in json.load(open(f"{ROOT}samples/debug/commitments_part{p}_extract.json", encoding="utf-8")).values():
        for pg in v["pages"]:
            ai[(p, pg["page_number"])] = v["forms"][0]
def render(p, n):
    pg = pymupdf.open(f"{ROOT}samples/commitments_part{p}.pdf")[n-1]
    pix = pg.get_pixmap(dpi=300); a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    ref = cv2.imread(f"{ROOT}samples/debug/commitments_part{p}_p{n}.png")  # oriented page the pipeline saw
    rs = lambda x: cv2.resize(x, (200, 200)).astype(float)
    best = min(range(4), key=lambda k: np.abs(rs(np.rot90(a, k)) - rs(ref)).mean())
    return np.ascontiguousarray(np.rot90(a, best))[:, :, ::-1], ref.shape, best
def aival(f, spec):
    if spec is None: return None
    if spec == "central": c = f["central_numbers"]; return c[0]["value"] if c else None
    if spec == "id": return (f["national_id"] or {}).get("value")
    if spec == "date": d = f["received_date"] or {}; return d.get("value") or d.get("raw_text")
    c = f["commitment_numbers"]; return c[0]["value"] if c else None
cache, out = {}, []
for p, n, field, kind, (x0, y0, x1, y1), spec in S:
    if (p, n) not in cache: cache[(p, n)] = render(p, n)
    img, refshape, rot = cache[(p, n)]
    sc = max(refshape[:2]) / 2000; m = 15
    c = img[max(0,int((y0-m)*sc)):int((y1+m)*sc), max(0,int((x0-m)*sc)):int((x1+m)*sc)]
    name = f"p{p}_pg{n}_{field}.png"
    cv2.imwrite("crops/" + name, c)
    out.append({"crop": name, "page": n, "part": p, "field": field, "kind": kind,
                "ai_value": aival(ai[(p, n)], spec), "truth": ""})
json.dump(out, open("truth.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(out), {k: rot for k, (_, _, rot) in cache.items()})
