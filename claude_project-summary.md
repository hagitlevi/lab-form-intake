# lab-form-intake: מצב הפרויקט והפרומפטים
עודכן: 21/10/2026 · גמר האקתון: 28/10/2026

> **להוראה לכל שיחה בפרויקט:** הקובץ הזה הוא מקור האמת למצב הפרויקט. כשהמשתמשת מבקשת "הפרומפט הבא", שולחים את הפרומפט הראשון בסעיף 8 שסטטוסו אינו ✅, עם ההסבר מה הוא עושה ואיך בודקים אותו. כשמשהו מתעדכן (שלב הסתיים, התקבלה החלטה), מזכירים לה לעדכן את הקובץ הזה ואת `CLAUDE.md` בריפו.

## 1. הפרויקט
מנוע שקורא טפסי התחייבות (PDF סרוק, צילומי טלפון, כתב יד), מחלץ 7 שדות, מסמן מה לא בטוח, ומציג ללבורנטית לבדיקה, תיקון ואישור. רשומה לכל התחייבות. האתגר של המעבדה המרכזית לנגיפים, האקתון AI קוד פתוח 2026.

**7 השדות:** שם פרטי, שם משפחה, ת"ז, מספרים מרכזיים (רשימה), תאריך קבלה, גורם שולח (מרשימה סגורה), מספרי התחייבות (רשימה).

**דרישות מהאתגר:** הפלט במערכת ולא בקובץ (ייצוא אופציונלי); שדה לא בטוח מסומן; שדה שלא נמצא נשאר ריק ולא מנוחש; לא לשלוח מידע החוצה בלי צורך ולא לשמור יותר מהנדרש; להגיש טבלת אמת של 10-15 טפסים עם דיווח כן על הדיוק. בתסריט של המארגנים: 30 טפסים, 3 שדות כתומים, "4 דקות במקום שעה". לכן סימון יתר הוא כישלון בדיוק כמו סימון חסר.

**איך לעבוד עם המשתמשת:** בעברית, הסברים מפורטים וישירים, בלי מחמאות. לומר כשמשהו לא נכון או מסובך מדי. בקוד: לציין באיזה קובץ ואיפה. פרומפטים ל-Claude Code באנגלית, מוכנים להעתקה. כל הצעה נבחנת מול הזמן שנשאר: עדיף פתרון פשוט שעובד.

## 2. חלוקת התפקידים (נקודה שנוטה להתבלבל)
| מי | מה |
|---|---|
| OCR (Tesseract) | מחלץ **את כל הטקסט** בעמוד, כמילים עם מיקומים. לא יודע איזו מילה היא איזה שדה |
| AI | מחלץ את **7 השדות מתמונות הטופס** (לא מטקסט ה-OCR). קורא כתב יד, חותמות והקשר |
| קוד פשוט | מקבץ עמודים לטפסים לפי תבניות מודפסות קבועות, ומשווה כל ערך של ה-AI למילים של ה-OCR ולברקוד |

השוואה מדויקת מול ה-OCR → השדה בטוח, ומקבלים ממנו מיקום להדגשה. אחרת → כתום. ה-AI לא "מאמת" את ה-OCR, והקוד בודק ששני מקורות קראו אותו דבר.

## 3. החלטות ארכיטקטורה ופריסה
- **סטאק:** Python 3.13, FastAPI, psycopg 3 + ConnectionPool, PostgreSQL, React + Vite שנבנה ומוגש מהשרת (שירות אחד, קישור אחד).
- **עיבוד:** PyMuPDF, OpenCV, Tesseract (heb+eng, מקומי), zxing-cpp, Anthropic SDK. רק תמונות הטפסים נשלחות החוצה.
- **פריסה:** Render, אזור **Frankfurt** לשרת ולמסד, Docker (בגלל Tesseract). המסד מתחבר ב-Internal Database URL. תמונות העמודים נשמרות במסד (BYTEA), כי מערכת הקבצים של Render זמנית.
- **עלויות:** מסד חינמי (פג 30 יום, מספיק עד הגמר). שרת חינמי (512MB, 0.1 מעבד) רק לבדיקת שלד. לפני בדיקות ערימה אמיתיות ולפני הגמר: שדרוג ל-Standard ($25, 2GB), שגם לא נכבה אחרי חוסר פעילות. אזור לא ניתן לשינוי אחרי יצירת שירות.
- **סביבת עבודה:** PyCharm + Claude Code, ריפו ושירות בשם `lab-form-intake`, תיקייה `C:\Users\chagi\PycharmProjects\lab-form-intake`.
- **דיאגרמות:** `ארכיטקטורה_מלא.pdf` (דף סקירה + א' ארכיטקטורה, ב' צינור, ג' קיבוץ עמודים, ד' עץ ביטחון, ה' טבלת עשרה מקרים).

## 4. הצינור
1. פיצול כל הקבצים לרשימת עמודים מסודרת (לפי סדר הקבצים במסך). עמודים ריקים מדולגים ומדווחים.
2. לכל עמוד, במקביל: בדיקת PDF דיגיטלי (שכבת טקסט **ובלי** תמונה על כל העמוד; סרוק עם OCR של הסורק לא נחשב דיגיטלי). אם סרוק: תיקון גרפי, OCR, ברקודים.
3. קיבוץ עמודים לטפסים (סעיף 5).
4. לכל טופס, במקביל: קריאה אחת ל-AI עם כל עמודי הטופס. התשובה יכולה להכיל כמה טפסים.
5. אימות וביטחון לכל שדה (סעיף 6).
6. שמירה כרשומות ממתינות, בדיקה ואישור במסך.

## 5. קיבוץ עמודים לטפסים (העיצוב החדש, הוחלט 21/10)
**ההחלטה:** ברירת המחדל הפוכה: **כל עמוד הוא טופס חדש**, ועמוד מצורף לקודם רק כשיש ראיה מודפסת ומפורשת. כך כשל של ה-OCR מוביל לפיצול (טעות גלויה) ולא לאיחוד (טעות שקטה).

הבדיקות לפי הסדר, עוצרים בראשונה שמתאימה:
1. ברקוד מפוענח בן 6 ספרות → טופס חדש. (לא בהכרח המספר המרכזי: בבני ציון ברקוד כזה הוא מספר התחייבות.)
2. מספר בן 6 ספרות ליד "מרכזי" מודפס (כולל OCR על חיתוכים מסובבים סביב ברקוד) → טופס חדש.
3. "עמוד/Page n מתוך/of m" עם n>1 → המשך של הקודם.
4. אחרת → טופס חדש.

**לא משמשים להחלטה:** "מספר התחייבות" או כותרות טופס, מדבקת מטופל, ת"ז שונה, מספר מקרה, קוד טופס בשוליים, כתב יד, המרקר הצהוב והשם/ת"ז בכתב יד כחול (שני האחרונים הם תוצר של הדוגמאות). הסיבה: טפסים עוקבים מאותה תבנית חולקים קוד טופס ולפעמים מספר מקרה, ולכן כללים כאלה היו מאחדים אותם. בדוגמאות גם כל הת"ז והשמות זהים, כך ש"ת"ז שונה" לא יעבוד עליהן.

**אחרי חילוץ ה-AI:**
- ה-AI מזהה בקבוצה שני טפסים (שני מספרים מרכזיים או שני מטופלים) → מפוצלת ומסומנת לבדיקה.
- טופס שחזר בלי שם, ת"ז, מספר מרכזי ומספר התחייבות → תגית "כנראה המשך של הטופס הקודם", עם כפתור "צירוף" (קריאת AI אחת מחדש לטופס המאוחד) וכפתור "זה טופס נפרד".

**`flagged` ברמת הטופס** מסמן גבול טופס לא ודאי. זה שונה מהכתום של שדה (סעיף 6).

**ההיסטוריה:** גרסה ראשונה צירפה כברירת מחדל ("אחרת, המשך") ויצרה 8 איחודים שגויים בדוגמאות; אחרי תיקון ראשון נשארו 4, כולם בהדפסות מסך של מערכת בית חולים (ה-OCR לא קורא את הכותרת בטבלה הצפופה). הגרסה החדשה עדיין לא נבנתה ולא נמדדה. אל תכתבו בדיווח "0 איחודים" לפני שנמדד.

## 6. ביטחון לכל שדה (ניסוח חיובי, שני מצבים בלבד: בטוח / לבדיקה)
לכל שדה, לפי הסדר: ה-AI מצא ערך? → הערך עובר את הכללים? → (רק מספר מרכזי) הברקוד מסכים? → מקור נוסף קרא בדיוק אותו ערך (מילות OCR, או ברקוד)? → **בטוח**. כל "לא" → כתום, עם סיבה בעברית. התאמה בתו אחד = כתום עם סימון התו.

כללים: ספרת ביקורת של ת"ז (ריפוד ל-9), תאריך תקין ולא עתידי, גורם שולח מהרשימה הסגורה (fuzzy), פורמט מספר התחייבות לפי שולח כשידוע. בדיקת ההכלה ב-OCR לא תופסת מספר נכון בשדה הלא נכון, ולכן הכללים באים קודם. התאמה מקרית אפשרית (למשל תאריך הביקור המודפס זהה לתאריך הקבלה), ואין לה נזק כשהערך זהה.

## 7. מה למדנו מהדוגמאות (חשוב ל-AI ולבדיקות)
- **מספר מרכזי:** מדבקה עם ברקוד + 6 ספרות + המילה "מרכזי" (מודפסת לאורך, ה-OCR לא קורא אותה). לפעמים כתב יד בלבד, למשל "947054-25.8" = מרכזי + תאריך (יום.חודש), בלי שנה.
- **מספר התחייבות, פורמטים שונים לפי שולח:** 2646 (שמיר, מדבקה), 141046589 (אסותא, מודפס), 11001 (ברזילי, כתב יד), 375365293 (כללית שרון, כתב יד), 7254627 (וולפסון, מדבקה הפוכה), X132767 (לניאדו), 2648 00879 (הלל יפה), 436702 (בני ציון, ברקוד של 6 ספרות שאינו מרכזי). "דגימה" היא מספר התחייבות רק בלאומית.
- **הדפסות מסך של מערכת בית חולים:** טפסים עצמאיים בלי מדבקה, מסובבים על הצד, עם טבלת "מספר התחייבות" (כמה שורות = כמה התחייבויות), מספר מרכזי ותאריך בכתב יד. אלה המקרים שגרמו לכל בעיות הקיבוץ. המארגנים סימנו בהן בצהוב "תיירות מרפא" (גורם שולח) ובדוגמת לאומית את התאריך המודפס של הביקור ואת "לאומית".
- **טפסים דו-עמודיים:** נדירים, למשל אסותא עם "Page 1 of 2".
- **פרטים אישיים הוחלפו** (שם ות"ז בכתב יד כחול, כולם זהים), והמרקר הצהוב הוא של המארגנים. אסור לבנות עליהם כללים.
- **ה-OCR:** לא יציב בטבלאות מודפסות צפופות (גופן מנוקד של הדפסת מסך), ובעמוד אחד קרא את "מספר התחייבות" ובעמוד אחר מאותה תבנית לא. מועמדים לתיקון: ערוץ אדום/מקסימום ערוצים (מעלים מרקר צהוב), הגדלה פי 2, `--psm 6`/`--psm 11`, או שני מעברים ואיחוד. עדיין לא נוסה.
- **כללי פרומפט ל-AI:** null במקום ניחוש; כל המספרים המרכזיים וכל מספרי ההתחייבות; בטבלת הדפסת מסך כל מספר בעמודה הוא התחייבות; שנה חסרה → משלימים משנת ההעלאה ומסמנים "השנה הושלמה"; תאריך שנלקח משדה תאריך הביקור (אין חותמת "התקבל") מסומן לבדיקה עם סיבה.

## 8. סביבה מקומית
- שרת: `python -m uvicorn backend.main:app --reload --reload-dir backend --port 8080` בתוך `.venv` (פורט 8000 חסום ב-Windows). להשתמש ב-**127.0.0.1** ולא ב-localhost, גם ב-proxy של Vite.
- מסד: `docker start labpg` (קונטיינר postgres:18 על פורט **5433**, כי 5432 תפוס ע"י PostgreSQL 13 מקומי). `DATABASE_URL=postgresql://postgres:dev@localhost:5433/postgres`.
- Tesseract הותקן ע"י Claude Code (UB Mannheim, winget); נתוני שפה ב-`%LOCALAPPDATA%\tessdata`; משתנים `TESSDATA_PREFIX` ו-`TESSERACT_CMD`.
- הדוגמאות ב-`samples/` (בגיט-איגנור): `commitments_part1.pdf` (26 עמודים), `commitments_part2.pdf` (22), `2pages.pdf` (עמוד ראשון ריק, אז 2 טפסים), `grouping_truth.json`; פלטי בדיקה ב-`samples/debug/`.
- פקודות בדיקה קיימות: `python -m backend.tools.scan_pdf <קובץ> [--page N] [--words]`, `python -m backend.tools.group_pdf <קבצים>`, `python -m unittest tests.test_grouping`.
- בטרמינל של Windows עברית מוצגת הפוכה ("יזכרמ"). לבדוק קבצי TSV ב-PyCharm או באקסל. קובצי `.tsv` נפתחים כברירת מחדל ב-ChatGPT: לשנות ב"פתח באמצעות".

## 9. סטטוס
| שלב | מצב |
|---|---|
| 1. שלד (שרת, מסד, ממשק ריק) | ✅ עובד |
| 2. עיבוד עמוד (פיצול, OCR, ברקוד) | ✅ נבנה. ה-OCR לא קורא היטב טבלאות צפופות |
| 3א. קיבוץ, גרסה ישנה | נבנה; 4 איחודים שגויים. **מוחלף ב-3ב** |
| 3ב. קיבוץ, עיצוב חדש | ⏳ **הבא** |
| 2ב. ניסוי הגדרות OCR | ⏳ |
| 2ג. מהירות ו-cache | ⏳ |
| 4-10 | ⏳ |
| 11. Docker ופריסה | ⏳ לעשות יחד אחרי שיש קוד עובד. עדיף לפרוס שלד כבר בימים הקרובים, לא בערב לפני הגמר |

## 10. ספריית הפרומפטים
**לפני כל פרומפט:** `CLAUDE.md` בריפו מעודכן (הגרסה מ-21/10). אחרי כל שלב: בודקים, commit, ורק אז הבא. בסיום כל שלב קראי את הסיכום של Claude Code בביקורתיות: הוא כבר טען דברים שלא נכונים (למשל שעמודים בלי ברקוד הם "המשך").

### פרומפט 3ב: קיבוץ חדש (הבא)
**מה זה עושה:** מחליף את לוגיקת הקיבוץ בעיצוב שבסעיף 5: כל עמוד טופס חדש, אלא אם יש ברקוד/מרכזי מודפס (טופס חדש) או "עמוד n מתוך m" (המשך). מוריד את כל סימני העמוד הראשון.
**איך בודקים:**
```
python -m unittest tests.test_grouping
python -m backend.tools.group_pdf samples/commitments_part1.pdf samples/commitments_part2.pdf samples/2pages.pdf
```
צפי: מספר הטפסים קרוב למספר העמודים (פחות עמודי ריק, פחות הטפסים הדו-עמודיים האמיתיים), **0 איחודים שגויים**, וכמה פיצולים שגויים בטפסים דו-עמודיים בלי מספור (זה מצופה, והם יסומנו בשלב 4). לפתוח את `samples/debug/*_groups.json` ולוודא שהכלל שהכריע בכל עמוד הגיוני.
```
Read CLAUDE.md (updated; section "Grouping" is the spec). Replace the grouping logic.

1. classify_page / group_pages: default = new form. Checks in order, stop at first match:
   (1) decoded 6-digit barcode → new form; (2) a 6-digit number directly next to a printed "מרכזי" in
   OCR text (also OCR a crop around each barcode rotated 90° and 270°) → new form;
   (3) "Page/עמוד n of/מתוך m" with n > 1 → continuation of the previous page; (4) otherwise → new form.
   Continuations may cross file boundaries. A first page of the batch with n > 1 has no previous
   page: new form, flagged.
2. Remove from the decision: form titles, "מספר התחייבות", patient sticker, different national ID,
   case number, form code, the "fallback" continuation rule. Do not use handwriting or the yellow
   highlighter / blue handwritten IDs. (You may keep extracted values as informational fields in the
   groups JSON.)
3. Check that the label string in the code is "מרכזי" in logical order (not "יזכרמ").
4. Report skipped blank pages in the output (skipped_pages), do not drop them silently.
5. Output per page: the rule that decided it. Keep the CLI python -m backend.tools.group_pdf
   <file1> [<file2> ...] and the groups JSON in samples/debug/. Rename per-page "central_number" to
   "central_barcode" and add "sticker_candidates" if not already done.
6. Update tests/test_grouping.py: single-page form with sticker; "Page 2 of 2" continuation; page
   with nothing → new form; two consecutive forms of the same template (must NOT merge); continuation
   across a file boundary; first page saying "page 2"; blank page reported.
7. Evaluate against samples/grouping_truth.json (written by the user, do not edit it): for each file
   print expected vs actual groups and count wrong merges and wrong splits separately. Run on
   both sample files and 2pages.pdf. Do not tune rules to a single page.
```

### פרומפט 2ב: ניסוי הגדרות OCR
**מה זה עושה:** בודק איזו הגדרת OCR קוראת את הטבלאות הצפופות של הדפסות המסך (למשל חלק 2 עמוד 20), בלי לשנות ברירת מחדל לפי עמוד אחד.
**איך בודקים:** הדוח של Claude Code מציג לכל וריאנט אילו מחרוזות נמצאו ובאיזה ביטחון. לאמת בעצמך: `Select-String -Path samples\\debug\\commitments_part2_p20_words.tsv -Pattern "2603203|2603204" -Encoding utf8`. לאמץ ברירת מחדל חדשה רק אם שום עמוד אחר לא מחמיר.
```
OCR misses the dense table on hospital-system screen printouts (e.g. part2 page 20: the header
"מספר התחייבות", 2603203, 2603204). Fix this in step 2 OCR only.
1. On the printout pages of both files, compare: current setup; grayscale from the red channel (or
   max of R,G,B) to remove yellow highlighter; 2x upscale; --psm 6; --psm 11. For each, report
   whether "מספר התחייבות", the commitment numbers and the labels are found, with confidence.
2. Run the best 1-2 variants on ALL pages of both files and compare with the current setup: total
   words, sticker candidates, "מרכזי" confirmations. Adopt only if nothing gets worse elsewhere.
3. If no single setup works for all pages, run two OCR passes (current + best variant) and merge
   the words, de-duplicating overlapping boxes.
```

### פרומפט 2ג: מהירות ו-cache
**מה זה עושה:** מדפיס התקדמות וזמנים לכל שלב, מריץ עמודים במקביל (`LOCAL_WORKERS`), ושומר תוצאות שלב 2 כדי ש-`group_pdf` לא יחשב הכול מחדש.
**איך בודקים:** ריצה שנייה של `group_pdf` על אותו קובץ צריכה לקחת שניות. לרשום את הזמן הממוצע לעמוד: ב-Render יש מעבד אחד, והדמו דורש 30 טפסים בדקות ספורות.
```
group_pdf on commitments_part1.pdf (26 pages) is slow. Please:
1. Print progress per page ("page 5/26 ... 4.2s") and at the end the total time and the average
   time per stage (render, orientation, OCR, barcodes, rotated crops).
2. Process pages in parallel with LOCAL_WORKERS (default 2) if not already.
3. Cache step-2 results per page in samples/debug/<file>_scan.json, keyed by file hash and page
   number. scan_pdf writes the cache; group_pdf reuses it unless --no-cache is given.
Do not change OCR settings in this task.
```

### פרומפט 4: חילוץ שדות עם AI
**מה זה עושה:** קריאה אחת ל-AI לכל טופס, עם כל עמודיו כתמונות. מחזיר את 7 השדות, ואם בקבוצה יש כמה טפסים, מחזיר כמה. אחר כך קוד מסמן טופס שחזר כמעט ריק כ"כנראה המשך".
**איך בודקים:** להריץ את ה-CLI על כמה טפסים ולהשוות לתמונה: האם המספר המרכזי והתאריך מכתב היד נקראו, האם בהדפסת מסך נוצרו כמה התחייבויות, והאם שדה לא קריא חזר null. לוודא שלא נשלח טקסט OCR ל-AI.
```
Read CLAUDE.md. Implement pipeline step 4: extract_form(page_images) with the Anthropic SDK.
- One request per form with all its page images (base64, resized to a sensible max), model from
  ANTHROPIC_MODEL, temperature 0. Do NOT send OCR text; the model reads the images independently.
- Pydantic response: a list of forms, each with the page indexes it covers and the 7 fields. Each field:
  value (or null), raw_text as written, origin (printed / handwritten / stamp / sticker).
- Prompt rules (from CLAUDE.md): null instead of guessing; return all central numbers and all
  commitment numbers; "דגימה" is a commitment number only on Leumit forms; sender from the closed list
  in backend/data/senders.json (HMOs + Israeli hospitals + "תיירות מרפא"); a handwritten
  "947054-25.8" is central number + received date (day.month); on hospital-system screen printouts
  every number in the table under "מספר התחייבות" is a separate commitment; if the year is missing
  return the date with origin and year_inferred=true; if a date is the visit date and there is no
  "התקבל" stamp, say so in a field date_source.
- If the pages hold more than one form (different patients, or different central numbers with their
  own commitment numbers), return them as separate forms.
- Validate with Pydantic; on invalid JSON retry once, sending the validation error back.
- After extraction (plain code): a form with no name, ID, central number and commitment number is
  flagged "probably a continuation of the previous form"; a group split by the AI is flagged.
- CLI: runs grouping + extraction on a PDF and prints fields per form. Stop after 5 forms.
```

### פרומפט 5: אימות וביטחון
**מה זה עושה:** מנרמל, מפעיל כללים, משווה כל ערך של ה-AI למילים של ה-OCR ולברקוד, ומחליט בטוח/לבדיקה עם סיבה בעברית ו-bbox.
**איך בודקים:** `python -m unittest` על כל ענף. בדיקה חשובה: ת"ז שזהה ל-OCR אבל נכשלת בספרת ביקורת חייבת לצאת "לבדיקה". אחר כך להריץ על כמה טפסים ולספור כמה שדות כתומים: בערך 3 מ-30 טפסים זה היעד, 40% מהשדות זה כישלון.
```
Read CLAUDE.md. Implement pipeline step 5: normalization, validation and the confidence decision,
exactly as the Confidence section describes.
- Normalize: digits only for IDs/numbers (keep a leading letter like X132767), ID padded to 9, dates to
  ISO (DD/MM, 2-digit years → 20xx), Hebrew final letters.
- Rules: Israeli ID check digit; date valid and not in the future; sender fuzzy-matched to the closed
  list (rapidfuzz); commitment-number regex per sender from a config file (start from what the samples
  show; unknown sender → no rule). year_inferred or date from the visit field → "review" with reason.
- Matching: search each value in the OCR words (exact, then one character different via rapidfuzz);
  take the bbox from the matching word(s). Central numbers are also compared with the barcode.
- Output per field: final value, state "safe"|"review", reasons (Hebrew, user-facing), bbox + page.
- Unit tests for every branch, including: an ID that matches OCR exactly but fails the check digit must
  be "review". Never store full OCR text.
```

### פרומפט 6: ריצת ערימה, שמירה ו-API
**מה זה עושה:** מחבר את כל הצינור לשרת ולמסד: העלאה, עיבוד ברקע במקביל, שורות שמופיעות בהדרגה, עריכה, צירוף/פיצול, אישור, חיפוש וייצוא.
**איך בודקים:** סקריפט בדיקה מקצה לקצה מול ה-API עם PDF הדוגמה; לפתוח `http://127.0.0.1:8080/docs` (Swagger) ולנסות את הנקודות. טופס אחד שנכשל לא מפיל את הערימה.
```
Read CLAUDE.md. Wire the full pipeline into the API and the database.
- POST /api/batches: multiple files in the order sent, store them, create the batch, process in the
  background, return batch_id immediately.
- Processing: per-page stage with a thread pool of LOCAL_WORKERS, then grouping, then the AI stage with
  AI_WORKERS. Save each finished form as soon as it is ready (status pending). One failing form must not
  fail the batch. Report skipped blank pages.
- GET /api/batches/{id}: progress (done/total) and rows so far, one row per commitment, with field
  states, reasons and form-level flags.
- GET /api/pages/{id}/image; PATCH /api/forms/{id} (validate, mark corrected and safe, write
  audit_log, one transaction, whitelist fields); POST /api/forms/{id}/merge-with-previous (re-extract the
  merged form with ONE AI call); POST /api/forms/{id}/mark-separate; POST /api/batches/{id}/approve.
- GET /api/records with search (ID, central number, commitment number) and status filter;
  GET /api/records/export.csv (UTF-8 with BOM so Excel shows Hebrew).
Test end-to-end with the sample PDF through the API with a small script.
```

### פרומפט 7: מסך קליטה וטבלת בדיקה
**מה זה עושה:** שני המסכים הראשונים של הממשק, בעברית ומיושרים לימין, לפי המוקאפ בקנבס.
**איך בודקים:** `npm run dev` ו-`http://127.0.0.1:5173`. להעלות קובץ ולראות שורות מופיעות בהדרגה, תאים כתומים עם סיבה, עריכה בתא, וסינון "רק לבדיקה".
```
Read CLAUDE.md. Build the first two screens in React (Hebrew, RTL).
Design: font Heebo; background #F4F7F9; white cards with #DDE3E8 borders; primary #0B6A85; "for
review" cells: background #FFF3E6, 2px border #E8892B, reason text #8A4500 under the value. Orange is
used ONLY for fields that need review. All numbers (IDs, central numbers, commitment numbers, dates) in
dir="ltr" with tabular numerals. Top bar: "המעבדה המרכזית לנגיפים" with links "קליטת טפסים", "רשומות".
1. Upload screen: large drop zone ("גוררים לכאן קבצים"), selected files that can be reordered by
   drag and removed, one primary button "התחלת עיבוד".
2. Review screen: progress "X מתוך Y"; an orange banner with the number of fields to review and a button to
   jump to the first; filter "הכל / רק לבדיקה"; a table with one row per commitment (page range, the 7
   fields, link to open the form); cells editable in place (PATCH on blur/Enter); rows appear while
   processing continues (poll every 1.5 s). Form-level tags: "ייתכן שזה המשך של טופס X" with
   "צירוף" / "זה טופס נפרד", and "פוצל לבדיקה". Show skipped blank pages as a notice.
   Button "אישור ושמירה".
```

### פרומפט 8: פאנל טופס בודד
**מה זה עושה:** תמונת הטופס עם מסגרת כתומה על השדה הנבחר, והשדות לצידה.
**איך בודקים:** ללחוץ על שורה, ולוודא שהמסגרת מדויקת, שיש זום וסיבוב, ושמעבר בין עמודי טופס עובד.
```
Read CLAUDE.md. Build the single-form view, opened from a row in the review table.
Layout: page image on one side (zoom, rotate, page tabs for multi-page forms) with an SVG overlay
drawing the selected field's bbox in orange; on the other side the form's fields as inputs, flagged ones
first with their reason. Focusing a field scrolls/zooms the image to its bbox. Sender is a select from
the closed list. Buttons: "אישור הטופס", "הבא לבדיקה" (next flagged field across the batch),
"צירוף לטופס הקודם", and add/remove for central and commitment numbers.
```

### פרומפט 9: מסך רשומות
**מה זה עושה:** חיפוש וסינון של כל הרשומות, וייצוא CSV.
**איך בודקים:** לחפש ת"ז ומספר מרכזי, לסנן ממתינות/אושרו, ולפתוח את ה-CSV באקסל ולוודא שהעברית תקינה.
```
Read CLAUDE.md. Build the records screen: search box (ID, central number or commitment number), status
filter "הכל / ממתינות / אושרו" with counts, a table with status badges (pending #FFF3E6/#8A4500,
approved #E5F3EA/#1D6B37), and a secondary button "ייצוא CSV". Clicking a row opens the single-form view.
```

### פרומפט 10: מדידת דיוק
**מה זה עושה:** משווה את הצינור לטבלת אמת שאת ממלאת ביד (10-15 טפסים), ומדווח דיוק לכל שדה, כמה סומנו, **וכמה ערכים שגויים סומנו "בטוח"** (המספר החשוב).
**איך בודקים:** `python -m backend.tools.evaluate`, ולפתוח את `samples/eval_report.md`. את טבלת האמת ממלאים אנשים בלבד, לפי הטפסים, ולא לפי הפלט של הצינור.
```
Read CLAUDE.md. Build an evaluation tool for the hackathon report.
- samples/ground_truth.json: one entry per form (page numbers and the correct 7 fields). Create the file
  structure with an empty template; the user fills it in by hand. Never fill it from pipeline output.
- python -m backend.tools.evaluate: run the pipeline on the samples, compare with the ground truth. Per
  field: accuracy, how many marked "review", and how many WRONG values were marked "safe". Also per
  sender. Write samples/eval_report.md.
```

### שלב 11: Docker ופריסה ל-Render (לעשות יחד)
**מה זה עושה:** ה-Dockerfile בונה שרת עם Python, Tesseract ועברית; Render בונה ממנו את השירות בכל push.
**איך בודקים:** ב-Render, לשונית Logs: הבנייה עוברת ומופיעה שורת uvicorn. אחר כך לקרוא `/api/health` בכתובת onrender.com, ולהעלות עמוד אחד ולראות שה-OCR בעברית עובד בענן.
```dockerfile
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-heb libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
```
(הממשק נבנה ב-`npm run build` ומוגש מ-FastAPI; לכן צריך גם שלב בנייה ל-frontend, או לבנות ולהוסיף את `frontend/dist` לפני ה-push.)
צעדים ב-Render: שירות חדש, Language: Docker, Region: Frankfurt; מסד PostgreSQL גם הוא בפרנקפורט; `DATABASE_URL` = ה-Internal Database URL; `ANTHROPIC_API_KEY` ושאר המשתנים ב-Environment (לא בקוד); לשדרג ל-Standard לפני בדיקות ערימה ולפני הגמר.

## 11. שאלות פתוחות למנטורית
- בהדפסות מסך של תיירות מרפא ("ללא קופ"ח"), האם "תיירות מרפא" הוא הערך הצפוי בגורם השולח?
- בהדפסות מסך בלי חותמת "התקבל": האם תאריך הקבלה הוא תאריך הביקור המסומן?
- טפסים בלי מדבקה מרכזית בכלל: מצב תקין, או טופס שעוד לא עבר קליטה?
- כשמופיעים גם בית חולים וגם קופה (שניידר + כללית), מי הגורם השולח?

## 12. מה לשים בדיווח להגשה
גישה, מגבלות, מה נדרש כדי שהמעבדה תשתמש בזה, ומספרים אמיתיים מהמדידה. נקודות שכדאי לציין: רק תמונות נשלחות החוצה; לא נשמר טקסט OCR מלא או מידע שאינו 7 השדות; בעמוד מערכת אמיתית התמונות יימחקו אחרי תקופת שמירה שהמעבדה תקבע; מידע רפואי בישראל עדיף לשמור בתוך ישראל; בדיקת ההכלה ב-OCR לא מגינה על שדה נכון במקום לא נכון, ולכן יש גם כללי אימות.
