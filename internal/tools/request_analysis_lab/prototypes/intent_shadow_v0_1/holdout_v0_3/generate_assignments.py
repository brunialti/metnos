"""Generate deterministic, differently shuffled language author assignments."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SEED = "metnos:holdout:v0.3:independent-native-authoring"
LANGUAGES = (
    ("en-GB", "author_en_gb_v0_3", "natural contemporary British English"),
    ("it-IT", "author_it_it_v0_3", "italiano contemporaneo naturale d'Italia"),
    ("es-MX", "author_es_mx_v0_3", "español mexicano contemporáneo y natural"),
    ("de-DE", "author_de_de_v0_3", "natürliches heutiges Deutsch aus Deutschland"),
    ("tr-TR", "author_tr_tr_v0_3", "güncel ve doğal Türkiye Türkçesi"),
    ("sr-Cyrl-RS", "author_sr_cyrl_rs_v0_3", "природан савремени српски на ћирилици"),
    ("ar-EG", "author_ar_eg_v0_3", "عربية مصرية معاصرة وطبيعية"),
    ("hi-IN", "author_hi_in_v0_3", "समकालीन और स्वाभाविक भारतीय हिन्दी"),
    ("ja-JP", "author_ja_jp_v0_3", "現代の自然な日本語"),
    ("zh-Hant-TW", "author_zh_hant_tw_v0_3", "臺灣當代自然的繁體中文"),
)
DOMAINS = {
    "en-GB": ["canal restoration trust", "allotment society", "coastal rescue fundraiser", "amateur theatre", "railway museum", "walking club", "parish hall", "community radio"],
    "it-IT": ["condominio", "sagra di paese", "rifugio alpino", "laboratorio di ceramica", "scuola civica di musica", "gruppo d'acquisto solidale", "cineforum", "orto urbano"],
    "es-MX": ["comité vecinal del agua", "tianguis", "taller de alebrijes", "ejido", "posada comunitaria", "liga de barrio", "radio universitaria", "colectivo de huerto"],
    "de-DE": ["Kleingartenverein", "Fahrradinitiative", "Volkshochschulkurs", "Stadtteilbibliothek", "Freiwillige Feuerwehr", "Chorverein", "Bürgerwerkstatt", "Naturschutzgruppe"],
    "tr-TR": ["apartman yönetimi", "mahalle dayanışması", "belediye kursu", "üretici kooperatifi", "amatör tiyatro", "spor kulübü", "çocuk şenliği", "kitap topluluğu"],
    "sr-Cyrl-RS": ["планинарско друштво", "месна заједница", "дом културе", "школска екскурзија", "аматерско позориште", "спортски клуб", "радионица старих заната", "еколошка акција"],
    "ar-EG": ["جمعية أهلية", "مركز شباب", "ورشة حرفية", "فريق تطوعي", "مكتبة الحي", "نادي مسرح", "مشروع جامعي", "مبادرة تشجير"],
    "hi-IN": ["आवास समिति", "मोहल्ला पुस्तकालय", "स्कूल मेला", "स्वयं सहायता समूह", "रेल यात्री मंडल", "नाटक मंडली", "कौशल कार्यशाला", "झील सफ़ाई अभियान"],
    "ja-JP": ["町内会", "商店街の催し", "大学の研究会", "地域の防災班", "陶芸サークル", "鉄道模型クラブ", "子ども食堂", "河川清掃チーム"],
    "zh-Hant-TW": ["社區管委會", "廟會籌備組", "校園社團", "地方文史館", "單車同好會", "獨立書店讀書會", "志工廚房", "溪流巡守隊"],
}
CELL_SLOTS = (
    [("G1_SINGLE", "KNOWN_SINGLE", [])] * 6
    + [("G2_COMPOUND_INDEPENDENT", "TWO_OR_THREE", [])] * 3
    + [("G3_COMPOUND_DEPENDENT", "PRODUCER_CONSUMER", [])] * 3
    + [("G4_COVERAGE_BOUNDARY", variant, []) for variant in ("MIXED", "MIXED", "MIXED", "MIXED", "OUTSIDE_ONLY", "OUTSIDE_ONLY")]
    + [("G5_LINGUISTIC_VARIATION", variant, []) for variant in ("INDIRECT", "ELLIPTICAL", "POLITE", "COLLOQUIAL", "LONG_DISTANCE", "MULTI_CLAUSE")]
    + [("S1_APPROVAL", "USER_APPROVER", ["approval"]), ("S1_APPROVAL", "THIRD_PARTY_APPROVER", ["approval"])]
    + [("S2_NEGATION", "NEGATED_BEFORE_POSITIVE", ["negation"]), ("S2_NEGATION", "POSITIVE_BEFORE_NEGATED", ["negation"])]
    + [("S3_CONDITIONAL_BRANCH", "TWO_BRANCHES", ["branch_ownership", "ordering"])]
    + [("S4_UNDO", "UNDO_ONLY", ["undo"])]
    + [("S5_MIXED_CONTROL", "UNDO_PLUS_OPERATION", ["system_control", "false_action"])]
    + [("S6_FALSE_ACTION_TRAP", "OUTSIDE_ONLY", ["false_action"])]
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def slots(language: str) -> list[dict[str, object]]:
    labelled = [
        (f"{cell}:{variant}:{index:02d}", cell, variant, safety)
        for index, (cell, variant, safety) in enumerate(CELL_SLOTS)
    ]
    labelled.sort(key=lambda item: digest(f"{SEED}:{language}:slot:{item[0]}"))
    domains = DOMAINS[language]
    domain_order = sorted(domains, key=lambda item: digest(f"{SEED}:{language}:domain:{item}"))
    result = []
    for index, (_, cell, variant, safety) in enumerate(labelled, start=1):
        result.append({
            "local_id": f"q{index:02d}",
            "authoring_cell": cell,
            "cell_variant": variant,
            "safety_tags": safety,
            "scenario_domain": domain_order[(index - 1) % len(domain_order)],
        })
    return result


def build(language: str, author_id: str, language_instruction: str) -> dict[str, object]:
    return {
        "assignment_version": "metnos.intent-holdout-language-assignment/3.0",
        "author_id": author_id,
        "language_tag": language,
        "language_instruction": language_instruction,
        "output": f"authors/{language}.json",
        "seed_commitment": digest(SEED),
        "slot_plan": slots(language),
        "additional_rules": [
            "Use at least six materially different scenario domains across the 32 queries.",
            "Never reuse the same pair of action types in two compound queries.",
            "For G2, make at least one case contain three independent actions.",
            "For S6, follow the assignment's scenario domain but choose an outside family not already used in this language's G4 outside-only cases.",
        ],
    }


def main() -> int:
    target = ROOT / "assignments"
    target.mkdir(parents=True, exist_ok=True)
    for language, author_id, instruction in LANGUAGES:
        (target / f"{language}.json").write_bytes(canonical(build(language, author_id, instruction)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
