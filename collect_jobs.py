# -*- coding: utf-8 -*-
"""공고 수집기. 잡알리오(중앙 공공기관)와 클린아이 잡플러스(지방공공기관)에서
환경·화학·화공 직렬 공고를 모아 data/jobs.json에 쓴다. build.py가 이 파일을 읽어 페이지에 박는다.

실행:  python3 collect_jobs.py            (Mac)
       python collect_jobs.py             (윈도우)
옵션:  --no-compete  과거 경쟁률 수집 생략 (빠름)
       --no-salary   신입 초임 수집 생략
환경변수 ALIO_SERVICE_KEY 가 있으면 공공데이터포털 공식 API로 진행 중 공고를 받고,
없으면 알리오 사이트 내부 주소(인증키 불필요)를 쓴다. 경쟁률과 초임은 항상 알리오 사이트에서 받는다.
외부 패키지 없이 표준 라이브러리만 쓴다.
"""
import json, os, re, sys, time, statistics
from datetime import date, datetime, timedelta
from urllib import request, parse, error

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
OUT = os.path.join(DATA_DIR, "jobs.json")
COMPETE_CACHE = os.path.join(DATA_DIR, "compete_cache.json")
SALARY_CACHE = os.path.join(DATA_DIR, "salary_cache.json")
os.makedirs(DATA_DIR, exist_ok=True)

TODAY = date.today()
KEY = os.environ.get("ALIO_SERVICE_KEY", "").strip()
NO_COMPETE = "--no-compete" in sys.argv
NO_SALARY = "--no-salary" in sys.argv

# 안내서 기관 ↔ 알리오 기관코드(pblntInstCd). 엔진 id는 engine_web.js의 기관 id.
TARGET = {
    "C0221": {"name": "한국수자원공사", "engine": ["kwater", "kwater_intern"]},
    "C0220": {"name": "한국수력원자력", "engine": ["khnp", "khnp_intern"]},
    "C0082": {"name": "한국서부발전", "engine": ["wp", "wp_intern"]},
    "C0259": {"name": "한국중부발전", "engine": ["komipo", "komipo_intern"]},
    "C0042": {"name": "한국남동발전", "engine": ["koen", "koen_intern"]},
    "C0043": {"name": "한국남부발전", "engine": ["kospo", "kospo_intern"]},
    "C0066": {"name": "한국동서발전", "engine": ["ewp", "ewp_intern"]},
    "C0400": {"name": "한국환경공단", "engine": ["keco"]},
    "C0046": {"name": "한국농어촌공사", "engine": ["ekr"]},
    "C0148": {"name": "한국가스안전공사", "engine": ["kgs"]},
    "C0147": {"name": "한국가스공사", "engine": ["kogas"]},
    "C0105": {"name": "인천국제공항공사", "engine": ["incheon"]},
    "C0215": {"name": "한국석유관리원", "engine": ["kpetro"]},
    "C0261": {"name": "한국지역난방공사", "engine": []},
}
# 직렬 키워드: 제목이나 전형 단계 이름에 이 말이 있으면 환경·화학 직렬로 본다
KW = ["환경", "화학", "화공", "수질", "대기", "폐기물", "토양", "소음", "상수도", "하수", "정수", "수처리"]
NCS_KW = ["환경", "화학"]
# 제목에 이 말이 있으면 환경 직렬이 아니거나(미화·청소) 동생이 지원할 수 없는 전형(장애인 제한)이라 뺀다
EXCL = ["미화", "청소", "경비", "주차", "방호", "운전", "조리", "장애", "합격자", "임용"]
HIRE_OK = ("정규직", "무기계약직", "청년인턴")          # 비정규직(기간제)은 뺀다
HIRE_BAD = ("비정규직",)
UA = {"User-Agent": "Mozilla/5.0 (gong-guide collector)"}
ALIO_INTERNAL = "https://opendata.alio.go.kr/new/odaApiMng/recrutInquiryAjax%s.do"
ALIO_REFERER = "https://opendata.alio.go.kr/new/odaApiMng/recrutInquiryList.do"
DATA_GO = "https://apis.data.go.kr/1051000/recruitment/%s"
CLEANEYE = "https://job.cleaneye.go.kr"
CE_SIDO = {"007001": "서울", "007002": "부산", "007003": "대구", "007004": "인천", "007006": "대전", "007007": "울산",
           "007017": "세종", "007008": "경기", "007009": "강원", "007010": "충북", "007011": "충남", "007012": "전북",
           "007013": "전남광주", "007014": "경북", "007015": "경남", "007016": "제주"}
CE_JOBTYPE = {"702001": "정규직", "702002": "무기계약직", "702003": "기간제", "702004": "전문계약직", "702005": "인턴",
              "702006": "일반계약직", "702007": "비상임"}
CE_EMPLOY = {"703001": "신입", "703002": "경력", "703003": "신입+경력"}
CE_JOBTYPE_OK = ("702001", "702002", "702005")


def log(*a):
    print(*a, flush=True)


def http(url, data=None, headers=None, retries=3, timeout=60):
    """GET(data=None) 또는 POST(form). 실패하면 잠깐 쉬고 다시 시도한다."""
    h = dict(UA); h.update(headers or {})
    body = parse.urlencode(data).encode() if isinstance(data, dict) else data
    for i in range(retries):
        try:
            req = request.Request(url, data=body, headers=h)
            with request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except (error.URLError, error.HTTPError, TimeoutError, OSError) as e:
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))


def jload(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsave(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def ymd(s):
    s = re.sub(r"\D", "", str(s or ""))[:8]
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else ""


def has_kw(text, kws=KW):
    t = str(text or "")
    return any(k in t for k in kws)


def hire_ok(name):
    n = str(name or "")
    return any(k in n for k in HIRE_OK) and not all(k in n for k in HIRE_BAD) if n else False


# ---------------- 잡알리오 ----------------
def alio_list(params):
    """목록 한 페이지. 반환 (rows, totalCount). 공식 API와 내부 주소의 응답 모양 차이를 흡수한다."""
    if KEY:
        q = dict(params); q.update({"serviceKey": KEY, "resultType": "json"})
        txt = http(DATA_GO % "list" + "?" + parse.urlencode(q))
        d = json.loads(txt)
        if "OpenAPI_ServiceResponse" in d:
            raise RuntimeError("공공데이터포털 오류: " + json.dumps(d, ensure_ascii=False)[:200])
        return d.get("result") or [], int(d.get("totalCount") or 0)
    txt = http(ALIO_INTERNAL % "List", dict(params), {"Referer": ALIO_REFERER, "X-Requested-With": "XMLHttpRequest"})
    d = json.loads(txt).get("data") or {}
    return d.get("result") or [], int(d.get("totalCount") or 0)


def alio_all(params, page_size=None):
    size = page_size or (100 if KEY else 1000)
    out, page = [], 1
    while True:
        p = dict(params); p.update({"numOfRows": size, "pageNo": page})
        rows, total = alio_list(p)
        out.extend(rows)
        if not rows or len(out) >= total or page > 60:
            break
        page += 1
    return out


def alio_detail(sn):
    """상세(전형 단계 steps, 첨부 files). 경쟁률은 공식 API 날짜 필터가 안 돼서 늘 내부 주소를 쓴다."""
    txt = http(ALIO_INTERNAL % "Detail", {"sn": sn}, {"Referer": ALIO_REFERER, "X-Requested-With": "XMLHttpRequest"})
    d = json.loads(txt).get("data") or {}
    r = d.get("result")
    return r[0] if isinstance(r, list) and r else (r or {})


def norm_alio(it):
    sn = it.get("recrutPblntSn")
    code = it.get("pblntInstCd") or ""
    return {
        "src": "alio", "id": f"alio-{sn}", "sn": sn, "code": code,
        "inst": re.sub(r"\(주\)|\(재\)", "", it.get("instNm") or "").strip(),
        "title": (it.get("recrutPbancTtl") or "").strip(),
        "hire": it.get("hireTypeNmLst") or "", "se": it.get("recrutSeNm") or "",
        "ncs": it.get("ncsCdNmLst") or "", "region": it.get("workRgnNmLst") or "",
        "edu": it.get("acbgCondNmLst") or "", "nope": it.get("recrutNope"),
        "beg": ymd(it.get("pbancBgngYmd")), "end": ymd(it.get("pbancEndYmd")),
        "url": f"https://job.alio.go.kr/recruitview.do?idx={sn}",
        "src_url": it.get("srcUrl") or "",
        "target": code in TARGET,
    }


def collect_alio_ongoing():
    rows = alio_all({"ongoingYn": "Y"})
    log(f"잡알리오 진행 중 공고 {len(rows)}건")
    jobs = []
    for it in rows:
        j = norm_alio(it)
        if not hire_ok(j["hire"]) or has_kw(j["title"], EXCL):
            continue
        if j["target"]:
            jobs.append(j); continue
        if "신입" in j["se"] and has_kw(j["ncs"], NCS_KW) and has_kw(j["title"]):
            jobs.append(j)
    log(f"  → 걸러낸 공고 {len(jobs)}건 (안내서 기관 {sum(1 for j in jobs if j['target'])}건)")
    return jobs


# ---------------- 클린아이 잡플러스 ----------------
def collect_cleaneye():
    """진행 중 공고를 페이지로 받아 제목 키워드와 고용형태로 거른다. 공식 API가 없어 사이트 목록 호출을 쓴다."""
    jobs, page, oldest = [], 1, (TODAY - timedelta(days=60)).isoformat()
    base = {"entRecruitList": "", "localYnList": "", "specialItemList": "", "jobTypeList": "", "employGbList": "",
            "workPlaceList": "", "officeHoursList": "", "reManYnList": "", "entGbList": "", "entKindList": "",
            "yearincome": "", "status": "709001", "pubDate": "", "pubEndDate": "", "entName": ""}
    seen = set()
    while page <= 80:
        p = dict(base); p["pageIndex"] = page
        try:
            d = json.loads(http(CLEANEYE + "/user/selectYpRecruitment.do", p,
                                {"Referer": CLEANEYE + "/user/ypRecruitment.do", "X-Requested-With": "XMLHttpRequest"}))
        except Exception as e:
            log("클린아이 호출 실패:", e); break
        rows = d.get("list") or []
        if not rows:
            break
        for x in rows:
            key = (x.get("empyear"), x.get("ypEntId"), x.get("entSeq"))
            if key in seen:
                continue
            seen.add(key)
            title = (x.get("entTitle") or "").strip()
            if not has_kw(title) or has_kw(title, EXCL) or x.get("jobType") not in CE_JOBTYPE_OK:
                continue
            q = parse.urlencode({"empyear": x.get("empyear"), "ypEntId": x.get("ypEntId"), "entSeq": x.get("entSeq")})
            jobs.append({
                "src": "cleaneye", "id": "ce-" + "-".join(str(k) for k in key), "code": "",
                "inst": (x.get("entName") or "").strip(), "title": title,
                "hire": CE_JOBTYPE.get(x.get("jobType"), ""), "se": CE_EMPLOY.get(x.get("employGb"), ""),
                "ncs": "", "region": CE_SIDO.get(x.get("sidoCd"), ""), "edu": "", "nope": None,
                "local": x.get("localYn") == "Y",
                "beg": x.get("pubDate") or "", "end": x.get("pubEndDate") or "",
                "url": CLEANEYE + "/user/ypCareersData.do?" + q, "src_url": "", "target": False,
            })
        if rows[-1].get("pubDate", "9999") < oldest:
            break
        page += 1
    log(f"클린아이 진행 중 공고 중 환경·화학 {len(jobs)}건 ({page}쪽)")
    return jobs


# ---------------- 과거 경쟁률 (알리오 공시) ----------------
def month_windows(months=36):
    """최근 months개월을 두 달 단위 (시작, 끝) 창으로 나눈다. 최근 2달은 전형이 끝나지 않아 뺀다.
    목록 조회는 공고 시작일 ≥ 시작, 마감일 ≤ 끝 으로 거르는 것으로 보여, 창 경계에 걸친 공고가 빠지지 않게 끝을 45일 늘린다(중복은 sn으로 걸러짐)."""
    end = TODAY.replace(day=1) - timedelta(days=1)
    end = end.replace(day=1) - timedelta(days=1)           # 두 달 전 말일
    wins = []
    while months > 0:
        start = (end.replace(day=1) - timedelta(days=1)).replace(day=1)
        wins.append((start.strftime("%Y%m%d"), (end + timedelta(days=45)).strftime("%Y%m%d")))
        end = start - timedelta(days=1); months -= 2
    return wins


def compete_from_steps(steps):
    """steps를 직렬(sortNo)별로 묶어 환경·화학 직렬의 수치를 낸다.
    서류 경쟁률 = 지원자 ÷ 서류 합격(첫 단계). 최종 경쟁률 = 지원자 ÷ 최종 선발(마지막 단계가 공시됐을 때만).
    공시가 cmpttRt를 채웠으면 그 값을 최종으로 쓴다. 기관이 숫자를 안 올리면 빈 목록."""
    groups = {}
    for s in steps or []:
        groups.setdefault(s.get("sortNo"), []).append(s)
    out = []
    for g, rows in groups.items():
        title = rows[0].get("recrutPbancTtl") or ""
        if not has_kw(title):
            continue
        rows.sort(key=lambda s: s.get("recrutStepSn") or 0)
        filled = [r for r in rows if r.get("aplyNope")]
        if not filled:
            continue
        first = filled[0]
        a, doc = first["aplyNope"], first.get("recrutNope")
        last = rows[-1]
        final = last.get("recrutNope") if last.get("recrutStepSn") == last.get("maxStepSn") else None
        rt = [r["cmpttRt"] for r in rows if r.get("cmpttRt")]
        rec = {"group": title.strip(), "aply": a,
               "doc_pass": doc, "doc_ratio": round(a / doc, 1) if doc else None,
               "final": final, "final_ratio": round(rt[-1], 1) if rt else (round(a / final, 1) if final else None),
               "steps": len(filled)}
        rec["ratio"] = rec["final_ratio"] or rec["doc_ratio"]      # 요약·중앙값에 쓰는 대표값
        if rec["ratio"]:
            out.append(rec)
    return out


def collect_compete():
    cache = jload(COMPETE_CACHE, {"done": {}})
    done = cache["done"]                                     # sn → {"end":..., "groups":[...]} (groups 비면 미공시)
    calls = 0
    for beg, end in month_windows(36):
        try:
            rows = alio_all({"ongoingYn": "A", "pbancBgngYmd": beg, "pbancEndYmd": end}, page_size=1000)
        except Exception as e:
            log("경쟁률 목록 실패", beg, end, e); continue
        for it in rows:
            j = norm_alio(it)
            if not j["target"] or not hire_ok(j["hire"]) or "신입" not in j["se"] or has_kw(j["title"], ["장애", "합격자"]):
                continue
            if not (has_kw(j["title"]) or has_kw(j["ncs"], NCS_KW) or "신입" in j["title"] or "공채" in j["title"] or "인턴" in j["title"]):
                continue
            sn = str(j["sn"]); prev = done.get(sn)
            # 이미 값이 있으면 건너뛴다. 값이 없던 공고는 마감 후 1년 안이면 다시 본다(공시가 늦게 붙는다)
            if prev and (prev.get("groups") or prev.get("end", "") < (TODAY - timedelta(days=365)).isoformat()):
                continue
            try:
                d = alio_detail(sn); calls += 1
            except Exception as e:
                log("상세 실패", sn, e); continue
            done[sn] = {"code": j["code"], "inst": j["inst"], "title": j["title"], "hire": j["hire"],
                        "end": j["end"], "groups": compete_from_steps(d.get("steps"))}
            time.sleep(0.3)
    cache["done"] = done; cache["updated"] = TODAY.isoformat()
    jsave(COMPETE_CACHE, cache)
    log(f"경쟁률: 상세 조회 {calls}건, 누적 {len(done)}공고")
    return summarize_compete(done)


def summarize_compete(done):
    """기관별: 전체 표본, 최근 값, 중앙값. 체험형 인턴은 따로 센다."""
    by = {}
    for sn, r in done.items():
        for g in r.get("groups") or []:
            kind = "intern" if "체험형" in r.get("hire", "") or "인턴" in r.get("title", "") else "regular"
            by.setdefault(r["code"], {"regular": [], "intern": []})[kind].append(
                {"sn": int(sn), "end": r["end"], "year": r["end"][:4], "title": r["title"], **g})
    out = {}
    for code, kinds in by.items():
        o = {"name": TARGET.get(code, {}).get("name", "")}
        for kind, recs in kinds.items():
            if not recs:
                continue
            recs.sort(key=lambda x: x["end"], reverse=True)
            fin = [r["final_ratio"] for r in recs if r.get("final_ratio")]
            doc = [r["doc_ratio"] for r in recs if r.get("doc_ratio")]
            o[kind] = {"n": len(recs), "latest": recs[0],
                       "median_final": round(statistics.median(fin), 1) if fin else None, "n_final": len(fin),
                       "median_doc": round(statistics.median(doc), 1) if doc else None, "n_doc": len(doc),
                       "records": recs[:12]}
        out[code] = o
    return out


# ---------------- 신입 초임 (알리오 경영공시) ----------------
def collect_salary():
    cache = jload(SALARY_CACHE, {})
    if cache.get("updated") and cache["updated"] >= (TODAY - timedelta(days=30)).isoformat():
        return cache["by"]
    base = "https://www.alio.go.kr/statisticsSearch/findSingleItemSearchList.json"
    year = None
    try:
        page = http("https://www.alio.go.kr/statisticsSearch/singleStatisticsSearch.do")
        m = re.search(r'alioYear["\']?\s*[:=]\s*["\']?(20\d\d)', page)
        year = m.group(1) if m else None
    except Exception:
        pass
    by = {}
    for item, key in (("GI0208", "first"), ("GI0101", "avg")):
        page = 1
        while page < 20:
            try:
                d = json.loads(http(base + "?" + parse.urlencode({"reportFormNo": "20601", "itemNo": item, "pageNo": page, "countPerPage": 100})))
            except Exception as e:
                log("초임 조회 실패", item, page, e); break
            rows = (d.get("data") or {}).get("result") or []
            if not rows:
                break
            for r in rows:
                cid = r.get("apbaId")
                if cid in TARGET:
                    v = r.get("yy0")
                    try:
                        v = int(float(v))
                    except (TypeError, ValueError):
                        v = None
                    by.setdefault(cid, {"name": r.get("apbaNa")})[key] = v
            if len(rows) < 100:
                break
            page += 1
    cache = {"updated": TODAY.isoformat(), "year": year, "by": by}
    jsave(SALARY_CACHE, cache)
    log(f"초임: {len(by)}기관 (기준연도 {year or '확인 필요'})")
    return by


# ---------------- 합치기 ----------------
def main():
    prev = jload(OUT, {})
    seen = prev.get("seen") or {}
    jobs = collect_alio_ongoing()
    try:
        jobs += collect_cleaneye()
    except Exception as e:
        log("클린아이 생략:", e)
    jobs = [j for j in jobs if not j["end"] or j["end"] >= TODAY.isoformat()]
    jobs.sort(key=lambda j: (j["end"] or "9999", not j["target"], j["inst"]))
    today = TODAY.isoformat()
    new = []
    for j in jobs:
        if j["id"] not in seen:
            seen[j["id"]] = today; new.append(j["id"])
        j["first_seen"] = seen[j["id"]]
        j["new"] = (date.fromisoformat(seen[j["id"]]) >= TODAY - timedelta(days=7))
    keep = set(j["id"] for j in jobs)
    seen = {k: v for k, v in seen.items() if k in keep or v >= (TODAY - timedelta(days=90)).isoformat()}

    compete = prev.get("compete") or {}
    if not NO_COMPETE:
        try:
            compete = collect_compete()
        except Exception as e:
            log("경쟁률 수집 실패, 이전 값 유지:", e)
    salary = prev.get("salary") or {}
    if not NO_SALARY:
        try:
            salary = collect_salary()
        except Exception as e:
            log("초임 수집 실패, 이전 값 유지:", e)
    sal_year = jload(SALARY_CACHE, {}).get("year")

    out = {
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "official" if KEY else "alio-site",
        "targets": {c: v["name"] for c, v in TARGET.items()},
        "engine_of": {c: v["engine"] for c, v in TARGET.items()},
        "jobs": jobs, "new": new, "seen": seen,
        "compete": compete, "salary": salary, "salary_year": sal_year,
    }
    jsave(OUT, out)
    log(f"저장: {OUT} | 공고 {len(jobs)}건, 새 공고 {len(new)}건, 경쟁률 {len(compete)}기관, 초임 {len(salary)}기관")


if __name__ == "__main__":
    main()
