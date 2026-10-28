# design-reference

Static HTML of the approved UI design (Hebrew, RTL). Open each file in a browser to see the target.
These are REFERENCE ONLY: do not import or ship them. Re-implement them as React components.
Each file is self-contained (its own <style>), so shared rules appear in several files.

| Screen | File | Screenshot |
|---|---|---|
| Upload (קליטת טפסים) | upload.html | screenshots/upload.png |
| Batch review table (בדיקת ערימה) | review.html | screenshots/review.png |
| Single form (בדיקת טופס) | form.html | screenshots/form.png |
| Records (רשומות) | records.html | screenshots/records.png |

## Design tokens
- Font: Heebo (Google Fonts, weights 400/500/700), fallback system-ui. Page background #F4F7F9, text #17232E.
- Header: WHITE bar with a 1px #DDE3E8 bottom border (not a dark/colored bar). Right side: two-line brand,
  "המעבדה המרכזית לנגיפים" (bold 17px) over "המרכז הרפואי שיבא" (13px, #5B6B7A). Left side: nav links
  "קליטת טפסים" / "רשומות"; the active one has background #E3F0F4 and color #084F63.
- Primary color #0B6A85 (hover #084F63). Primary button: white text, radius 10px, min-height 44px.
  Secondary button: white background, 1px #C9D3DB border.
- Cards: white, 1px #DDE3E8 border, radius 14px. Max content width 1280px (upload screen 760px).
- "For review" cells: background #FFF3E6, 2px inset border #E8892B, small reason text #8A4500 under the value.
  Orange is used ONLY for fields that need review. Banner: #FFF3E6 with #F2C28E border.
- Status badges: pending bg #FFF3E6 / text #8A4500; approved bg #E5F3EA / text #1D6B37.
- All numbers (IDs, central numbers, commitment numbers, dates) use dir="ltr", unicode-bidi: isolate,
  font-variant-numeric: tabular-nums.
- Tables: header row #F8FAFB with 13px #4F5F6E text; rows 15px with #EEF1F4 separators; hover #F8FBFC.
- Dashed drop zone: 2px dashed #9FB4C2, radius 16px, white, with an upload icon, title and a
  "בחירת קבצים" button. Spacing is generous: do not compress it.
