# oikb-sync: מדריך מלא

סנכרון אוטומטי של בסיסי ידע (Knowledge Bases) ב-Open WebUI מ-GitHub, GitLab, Gitea, Bitbucket, Confluence ו-SharePoint.

המשתמש יוצר בסיס ידע וכותב בתיאור שלו מאיפה למשוך את התוכן ואיך להתחבר. משם הכל אוטומטי. אף אחד לא עורך קבצי YAML ולא פורס מחדש כשמוסיפים, משנים או מוחקים בסיסי ידע.

המדריך הטכני באנגלית, כולל כל ההגדרות: [`charts/oikb-sync/README.md`](../../charts/oikb-sync/README.md).

---

## תוכן עניינים

1. [איך המערכת עובדת](#איך-המערכת-עובדת)
2. [מי אחראי על מה](#מי-אחראי-על-מה)
3. [דרישות מוקדמות והכנת Open WebUI](#דרישות-מוקדמות-והכנת-open-webui)
4. [התקנה](#התקנה)
5. [חיבור בסיס ידע (למשתמשים)](#חיבור-בסיס-ידע-למשתמשים)
6. [המקורות הנתמכים ואיך מגדירים כל אחד](#המקורות-הנתמכים-ואיך-מגדירים-כל-אחד)
7. [מה נתמך, מה לא, ולמה](#מה-נתמך-מה-לא-ולמה)
8. [מה קורה מתי](#מה-קורה-מתי)
9. [הגדרות ה-chart](#הגדרות-ה-chart)
10. [אבטחה](#אבטחה)
11. [תפעול](#תפעול)
12. [GitOps (Argo CD)](#gitops-argo-cd)
13. [תקלות נפוצות](#תקלות-נפוצות)
14. [שדרוג והסרה](#שדרוג-והסרה)

---

## איך המערכת עובדת

```mermaid
sequenceDiagram
    actor U as משתמש
    participant OW as Open WebUI
    participant R as Reconciler
    participant S as Secret (.oikb.yaml)
    participant D as oikb daemon
    participant G as המקור (GitLab...)

    U->>OW: יוצר בסיס ידע + שורות oikb בתיאור
    U->>OW: משתף עם משתמש השירות (Write)
    R->>OW: כל 5 דקות: רשימת בסיסי הידע
    R->>R: בונה .oikb.yaml מהתיאורים
    alt הקונפיגורציה השתנתה
        R->>S: כותב את הקונפיגורציה
        R->>D: restart (או 0/1 רפליקות)
    else לא השתנתה
        R->>R: לא עושה כלום
    end
    loop לכל בסיס ידע: מיד בעלייה, ואז כל interval
        D->>G: רשימת קבצים + checksum
        D->>OW: השוואה (/sync/diff)
        D->>OW: מחיקת ישנים, העלאת חדשים ושונים
    end
```

**בשלושה משפטים:**

1. **ה-reconciler** קורא את התיאורים של בסיסי הידע. הוא רץ כל 5 דקות, וגם פעם אחת מיד אחרי כל `helm install` או `helm upgrade`. מהתיאורים הוא בונה את קובץ הקונפיגורציה של oikb.
2. **ה-daemon של oikb** מושך את הקבצים מהמקור, משווה אותם למה שכבר נמצא בבסיס הידע, ומעלה רק את מה שהשתנה.
3. **Open WebUI** שומר את הקבצים ומאנדקס אותם, כך שהמודל יכול להשתמש בהם בצ'אט.

ה-reconciler הוא **רק צינור**: הוא לא מסנכרן כלום בעצמו.

**הסנכרון אינקרמנטלי.** Open WebUI שומר לכל קובץ את ה-checksum שהיה לו בזמן ההעלאה. בכל סנכרון oikb שולח את ה-checksums העדכניים מהמקור, ו-Open WebUI מחזיר מה נוסף, מה השתנה ומה נמחק. רק קבצים כאלה מועלים או נמחקים. סנכרון שבו לא השתנה כלום כמעט לא עולה משאבים, ולכן restart או interval תכוף לא מכבידים.

**הסנכרון חד-כיווני.** המקור הוא מקור האמת:
- קובץ שנערך בתוך Open WebUI שומר את ה-checksum הישן, ולכן oikb לא מזהה את העריכה ולא משחזר את הקובץ.
- קובץ שהוסיפו ידנית לבסיס ידע מסונכרן נמחק בסנכרון הבא, כי הוא לא קיים במקור.

---

## מי אחראי על מה

| מי | אחראי על | **לא** אחראי על |
|---|---|---|
| **המשתמש** | ליצור את בסיס הידע, לכתוב את שורות `oikb` בתיאור, לספק טוקן תקין, לשתף עם משתמש השירות | שום דבר ב-Kubernetes |
| **צוות הפלטפורמה** | התקנת ה-chart, הכנת משתמש השירות ומפתח ה-API שלו, רשת ו-proxy | הגדרות של בסיסי ידע ספציפיים |
| **ה-reconciler** | לקרוא תיאורים, לבנות מהם את הקונפיגורציה, לכתוב אותה ל-Secret, להפעיל מחדש את ה-daemon כשמשהו משתנה | לסנכרן, לגשת למקורות, ליצור או למחוק בסיסי ידע |
| **ה-daemon של oikb** | לסנכרן כל בסיס ידע מהמקור שלו לפי ה-interval | לזהות בסיסי ידע חדשים או לקרוא תיאורים |
| **Open WebUI** | לשמור קבצים, לחשב את ההבדלים, לאנדקס לחיפוש, לאכוף הרשאות | להחליט מה לסנכרן |

---

## דרישות מוקדמות והכנת Open WebUI

| דרישה | למה |
|---|---|
| Open WebUI בגרסה 0.9.6 ומעלה | oikb משתמש ב-endpoints של סנכרון אינקרמנטלי שנוספו בגרסה הזו |
| Helm 3.8 ומעלה | |
| Kubernetes 1.23 ומעלה, או OpenShift 4.10 ומעלה | |
| גישת רשת מה-namespace ל-Open WebUI ולמקורות | אם צריך proxy, מגדירים `proxy.*`. OpenShift **לא** מזריק את ה-proxy של הקלאסטר ל-pods של אפליקציות |
| גישה ל-images `ghcr.io/open-webui/oikb:0.6.0` ו-`docker.io/library/python:3.12-alpine` | אפשר להחליף ל-mirror דרך `daemon.image` ו-`reconciler.image` |

### הכנת Open WebUI (פעם אחת, על ידי אדמין)

**מפתחות API כבויים כברירת מחדל ב-Open WebUI,** ומשתמשים רגילים לא מורשים ליצור אותם. לכן:

1. **להפעיל מפתחות API:** ב-Admin Settings, בלשונית General, מפעילים את *Enable API Keys*. לחלופין מגדירים `ENABLE_API_KEYS=true` ב-Deployment של Open WebUI.
2. **ליצור משתמש שירות** בתפקיד *user* רגיל, למשל `oikb-sync@example.com`. זה חשבון של המערכת, לא של אדם. גם ה-reconciler וגם ה-daemon פועלים בשמו, ולכן הם רואים ויכולים לכתוב רק לבסיסי ידע שמשותפים איתו.
3. **לתת לו הרשאה להשתמש במפתחות API:** מוסיפים אותו לקבוצה, למשל `oikb-sync`, שיש לה הרשאת *API Keys*. ההרשאה נמצאת ב-Admin, Users, Groups, ואז בהרשאות של הקבוצה בחלק של Features. לחלופין אפשר להפעיל את ההרשאה לכל המשתמשים עם `USER_PERMISSIONS_FEATURES_API_KEYS=true`.
4. **אם מופעלת הגבלת endpoints למפתחות** (`ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS`): מוסיפים את `/api/v1/knowledge,/api/v1/files` לרשימה ב-`API_KEYS_ALLOWED_ENDPOINTS`.
5. **להתחבר כמשתמש השירות וליצור מפתח:** Settings, Account, API keys. זה הערך של `openWebUI.apiKey`.

**למה צריך לשתף עם משתמש השירות?** Open WebUI מחזיר למשתמש רק את בסיסי הידע שלו, ואת אלה שמשותפים איתו או עם קבוצה שהוא חבר בה. בנוסף, סנכרון דורש הרשאת כתיבה. השיתוף הוא גם **הסכמה מפורשת** של בעל בסיס הידע לכך שהמערכת תיגע בו. אפשר לשתף עם המשתמש עצמו או עם הקבוצה `oikb-sync`.

---

## התקנה

### מינימלית

```yaml
# my-values.yaml
openWebUI:
  url: http://open-webui.open-webui.svc.cluster.local:8080
  apiKey: sk-...
```

```bash
helm install oikb-sync ./charts/oikb-sync -n oikb --create-namespace -f my-values.yaml
```

- `openWebUI.url` היא **כתובת הבסיס** של Open WebUI, כפי שאפשר להגיע אליה מה-namespace `oikb`. אם Open WebUI נמצא ב-namespace אחר, צריך לכתוב את שם ה-DNS המלא: `http://<service>.<namespace>.svc.cluster.local:<port>`.
- מיד אחרי ההתקנה רץ Job חד-פעמי של ה-reconciler. **אם הוא נכשל,** למשל בגלל כתובת שגויה, מפתח שגוי או חוסר גישה לרשת, גם `helm install` נכשל. את הסיבה רואים בלוג:
  ```bash
  kubectl -n oikb logs job/oikb-sync-reconciler-init
  ```
- **ה-Deployment של ה-daemon מציג 0 רפליקות עד שמחברים בסיס ידע ראשון.** זה תקין: oikb לא יכול לעלות עם קונפיגורציה ריקה, ולכן ה-reconciler מעלה אותו לרפליקה אחת רק כשיש מה לסנכרן.

### עם Secret קיים, כדי לא לשמור את המפתח ב-values

```bash
kubectl -n oikb create secret generic oikb-keys \
  --from-literal=open-webui-api-key=sk-... \
  --from-literal=oikb-api-key="$(openssl rand -hex 32)"
```

```yaml
openWebUI:
  url: http://open-webui.open-webui.svc.cluster.local:8080
existingSecret: oikb-keys
```

### מאחורי proxy

```bash
oc get proxy cluster -o jsonpath='{.status.httpsProxy}{"\n"}{.status.noProxy}{"\n"}'
```

```yaml
proxy:
  httpProxy: http://proxy.example.com:3128
  httpsProxy: http://proxy.example.com:3128
  noProxy: .corp.example.com,10.0.0.0/8     # אופציונלי, בנוסף לרשימה האוטומטית
```

ה-chart מוסיף ל-`NO_PROXY` אוטומטית את הכתובת של Open WebUI, ואת `.svc`, `.cluster.local`, `localhost` ו-`127.0.0.1`, כך שהתעבורה ל-Open WebUI אף פעם לא עוברת דרך ה-proxy. גם הקריאות של ה-reconciler ל-API של Kubernetes תמיד עוקפות את ה-proxy.

### OpenShift

ה-chart מזהה OpenShift לבד, לפי ה-API `security.openshift.io`, ולא מגדיר UID קבוע. כך ה-SCC `restricted-v2` מקצה UID בעצמו. ב-Kubernetes רגיל ה-chart מגדיר UID קבוע שאינו root. אם הזיהוי נכשל, מגדירים `platform: openshift` או `platform: kubernetes`.

---

## חיבור בסיס ידע (למשתמשים)

1. **יוצרים בסיס ידע** ב-Open WebUI. **השם חופשי.** המערכת מזהה בסיסי ידע לפי ה-ID שלהם ומתעלמת מהשם.
2. **כותבים את שורות `oikb` בתיאור.** כל טקסט אחר בתיאור לא משפיע על הסנכרון.
   ```
   Operations runbooks of the platform team.

   oikb: gitlab:platform/ops/runbooks
   oikb-branch: main
   oikb-path: docs/
   oikb-base-url: https://gitlab.example.com
   oikb-token: glpat-xxxxxxxxxxxxxxxxxxxx
   oikb-interval: 1h
   ```
3. **משתפים עם משתמש השירות, או עם הקבוצה שלו, בהרשאת Write.** בלי שיתוף המערכת לא רואה את בסיס הידע. עם הרשאת קריאה בלבד הוא מדולג, עם אזהרה בלוג.
4. **מחכים עד 5 דקות.** ה-daemon עולה מחדש ומסנכרן מיד. הקבצים מופיעים בבסיס הידע, ו-Open WebUI מאנדקס אותם ברקע, כך שייתכן שייקח רגע עד שאפשר לחפש בהם.

### כללי הכתיבה

- הפורמט הוא `oikb-<שדה>: <ערך>`. השורה היחידה שחובה היא `oikb: <מקור>`.
- אין הבדל בין אותיות גדולות לקטנות. מקף הופך לקו תחתון, כך ש-`oikb-base-url` הוא השדה `base_url`. אם שדה מופיע פעמיים, הראשון קובע.
- אפשר לכתוב שורה אחת לכל שדה, או כמה שדות בשורה אחת. זה חשוב: **עמוד העריכה** של בסיס ידע ב-Open WebUI מציג את התיאור בשדה של שורה אחת ומוחק ירידות שורה. אחרי עריכה שם כל השדות מתחברים לשורה אחת, והמערכת מתמודדת עם זה.
- שורת `oikb-...` שהמערכת לא מכירה מתעלמים ממנה, עם אזהרה בלוג.

### שדות כלליים, לכל המקורות

| שורה | חובה? | משמעות | ברירת מחדל |
|---|---|---|---|
| `oikb:` | כן | מחרוזת המקור, ראו בהמשך | – |
| `oikb-interval:` | לא | תדירות הסנכרון: `30m`, `1h`, `6h`, `1d`, או ביטוי cron כמו `0 6 * * 1-5` | `1h` (`reconciler.defaultInterval`) |
| `oikb-branch:` | לא | ענף, במקורות git | הענף הראשי של הריפו |
| `oikb-path:` | לא | לסנכרן רק תיקייה אחת, במקורות git וב-SharePoint. הנתיבים בבסיס הידע יחסיים לתיקייה הזו | כל הריפו או הספרייה |

---

## המקורות הנתמכים ואיך מגדירים כל אחד

### GitHub

| | |
|---|---|
| מקור | `github:owner/repo`, או `github:owner/repo/sub/folder` |
| התחברות | `oikb-token`: **fine-grained personal access token** עם הרשאת **Contents: Read-only** לריפו הספציפי, או classic token עם `repo`. בריפו ציבורי אפשר בלי |
| חשוב | בלי טוקן GitHub מאפשר **60 בקשות בשעה לכל כתובת IP**, והן משותפות לכל הקלאסטר. oikb שולח בערך בקשה אחת לכל קובץ שהוא מוריד, ולכן סנכרון ראשון של ריפו ציבורי עם יותר מכ-50 קבצים ייכשל על מגבלת קצב. לריפו כזה כדאי להוסיף טוקן |

```
oikb: github:my-org/handbook
oikb-branch: main
oikb-token: github_pat_xxx
```

### GitLab (gitlab.com או שרת פנימי)

| | |
|---|---|
| מקור | `gitlab:group/project`, `gitlab:group/subgroup/project`, `gitlab:group/project:path/to/dir`. ל-Wiki: `gitlab:group/project?wiki=true` |
| התחברות | `oikb-token`: **Project Access Token** בתפקיד Reporter, או טוקן אישי, עם scope **`read_api`**. `read_repository` לבד לא מספיק, כי oikb קורא דרך ה-REST API. בפרויקט ציבורי אפשר בלי |
| `oikb-base-url` | רק לשרת פנימי: כתובת השורש, **בלי** `/api/v4`. ברירת המחדל היא `https://gitlab.com` |

```
oikb: gitlab:platform/ops/runbooks
oikb-base-url: https://gitlab.example.com
oikb-token: glpat-xxx
```

### Gitea

| | |
|---|---|
| מקור | `gitea:owner/repo`, `gitea:owner/repo/sub/folder`. ל-Wiki: `gitea:owner/repo?wiki=true` |
| התחברות | `oikb-token`: טוקן עם `read:repository`. בריפו ציבורי אפשר בלי |
| `oikb-base-url` | **תמיד חובה,** כי ל-Gitea אין שרת ברירת מחדל. כתובת השורש, בלי `/api/v1` |

```
oikb: gitea:engineering/docs
oikb-base-url: https://git.example.com
oikb-token: xxx
```

### Bitbucket Cloud

| | |
|---|---|
| מקור | `bitbucket:workspace/repo`, או `bitbucket:workspace/repo/sub/folder` |
| התחברות | `oikb-user` ו-`oikb-app-password`, שניהם או אף אחד מהם. **Atlassian ביטלה את ה-app passwords ב-9 ביוני 2026.** במקומם משתמשים ב-**API token**: `oikb-user` הוא **כתובת המייל של חשבון Atlassian**, ו-`oikb-app-password` הוא **ה-API token**, עם הרשאת קריאה לריפו. בריפו ציבורי אפשר בלי |
| חשוב | רק Bitbucket Cloud (`api.bitbucket.org`). oikb לא תומך ב-Bitbucket Server או Data Center. גם כאן יש מגבלת קצב נמוכה בלי טוקן |

```
oikb: bitbucket:acme/docs
oikb-user: bot@acme.com
oikb-app-password: ATATT3xxx
```

### Confluence (Cloud או Data Center)

| | |
|---|---|
| מקור | `confluence:SPACEKEY`. עם `confluence:SPACEKEY?structure=hierarchical` עץ הדפים נשמר כתיקיות. אפשר גם לכלול את הכתובת במקור: `confluence:https://acme.atlassian.net/wiki/ENG` |
| Cloud | `oikb-base-url: https://<site>.atlassian.net/wiki`, `oikb-user: <מייל>`, `oikb-token: <Atlassian API token>` |
| Data Center או Server | `oikb-base-url: https://confluence.example.com`, `oikb-token: <personal access token>` (בלי `oikb-user`, ההתחברות היא Bearer), `oikb-api-version: v1` |
| חובה | `oikb-token`, ו-`oikb-base-url` אם הכתובת לא כלולה במקור |

```
oikb: confluence:ENG
oikb-base-url: https://acme.atlassian.net/wiki
oikb-user: bot@acme.com
oikb-token: ATATT3xxx
oikb-interval: 0 6 * * 1-5
```

### SharePoint Online

| | |
|---|---|
| מקור | `sharepoint:<host>/<library>`, `sharepoint:<host>/sites/<site>/<library>`, או `sharepoint:<host>/sites/<site>/<subsite>::<library>` |
| התחברות | שלושה שדות, כולם חובה: `oikb-tenant-id`, `oikb-client-id`, `oikb-client-secret`. אלה הפרטים של App Registration ב-Entra ID עם הרשאת **Application** ל-Microsoft Graph: `Sites.Read.All` עם admin consent, או `Sites.Selected` עם הרשאה לאתר הספציפי |
| `oikb-cloud` | `commercial` (ברירת מחדל), `gcc_high` או `dod` |
| לא נתמך | התחברות עם תעודה, כי היא דורשת קובץ |

```
oikb: sharepoint:contoso.sharepoint.com/sites/Legal/Documents
oikb-tenant-id: 00000000-0000-0000-0000-000000000000
oikb-client-id: 11111111-1111-1111-1111-111111111111
oikb-client-secret: xxx
```

### סיכום שדות ההתחברות

| מקור | שורות | בלי פרטי התחברות |
|---|---|---|
| `github:` | `oikb-token` | ✅ ריפו ציבורי (עם מגבלת קצב) |
| `gitlab:` | `oikb-token`, `oikb-base-url` | ✅ פרויקט ציבורי |
| `gitea:` | `oikb-token`, `oikb-base-url` (חובה) | ✅ ריפו ציבורי (עדיין צריך `oikb-base-url`) |
| `bitbucket:` | `oikb-user`, `oikb-app-password` | ✅ ריפו ציבורי (עם מגבלת קצב) |
| `confluence:` | `oikb-base-url`, `oikb-user`, `oikb-token`, `oikb-api-version` | ❌ |
| `sharepoint:` | `oikb-tenant-id`, `oikb-client-id`, `oikb-client-secret`, `oikb-cloud` | ❌ |

כדי לחייב פרטי התחברות בכל בסיס ידע, מגדירים `reconciler.allowAnonymous: false`.

---

## מה נתמך, מה לא, ולמה

| יכולת | נתמך? | למה, ובגלל מי |
|---|---|---|
| GitHub, GitLab, Gitea, Bitbucket Cloud, Confluence, SharePoint | ✅ | oikb 0.5.0 ומעלה מעביר לחיבורים האלה פרטי התחברות נפרדים לכל מקור (`auth:`) |
| ריפו git ציבורי בלי טוקן | ✅ | אפשר לכבות עם `reconciler.allowAnonymous` |
| Jira, Notion, Slack, Zendesk, ServiceNow, Linear, Asana, ClickUp, Airtable, HubSpot, Salesforce, Outline, Discourse, BookStack, DokuWiki, Freshdesk, GitBook, Guru, Slab, Document360, Discord, Teams, Zulip, XenForo, Egnyte, Dropbox, Nextcloud, Gong, Fireflies, Google Sites, Google Drive, Gmail, ProductBoard, Zotero, Azure Blob | ❌ | **מגבלה של oikb.** החיבורים עצמם מקבלים פרטי התחברות, אבל oikb 0.6.0 לא מעביר להם את `auth:`, ולכן הם קוראים רק ממשתני סביבה גלובליים, משותפים לכל בסיסי הידע. ביקשנו תיקון: [`docs/upstream-feature-request.md`](../upstream-feature-request.md) |
| S3, R2, GCS, Oracle Cloud Storage | ❌ | **מגבלה של oikb.** החיבורים האלה מקבלים פרטי התחברות רק דרך משתני סביבה או ה-SDK של הענן |
| טוקן אחד משותף לכל בסיסי הידע | ❌ (בכוונה) | **החלטה שלנו.** כל משתמש היה יכול לסנכרן תוכן שאין לו הרשאה לראות |
| נתיב מקומי (`./...`) והסורק `web:` | ❌ (בכוונה) | **החלטה שלנו.** הם היו מאפשרים לקרוא קבצים מתוך ה-pod או להגיע לכתובות פנימיות בקלאסטר |
| SharePoint עם תעודה | ❌ | **מגבלה שלנו.** זה דורש קובץ, וה-reconciler מעביר רק ערכים טקסטואליים |
| Bitbucket Server או Data Center | ❌ | **מגבלה של oikb.** החיבור עובד רק מול Bitbucket Cloud |
| filters, concurrency, notify, webhooks | ❌ (עדיין) | **מגבלה שלנו.** oikb תומך בהם, אבל ה-reconciler עוד לא חושף אותם |
| שחזור קובץ שנערך בתוך Open WebUI | ❌ | **ההתנהגות של Open WebUI.** ההשוואה נעשית מול ה-checksum מזמן ההעלאה, ועריכה בממשק לא משנה אותו |
| שינוי קונפיגורציה בלי restart של ה-daemon | ❌ | **מגבלה של oikb.** ה-daemon קורא את הקונפיגורציה רק בעלייה |
| יותר מרפליקה אחת של ה-daemon | ❌ | **מגבלה של oikb.** התזמון והנעילות נמצאים בתהליך אחד |
| בסיסי ידע חיצוניים (External) | ❌ | **מגבלה של Open WebUI.** הם לקריאה בלבד |

---

## מה קורה מתי

### תזמונים

| מה | מתי |
|---|---|
| ריצת reconciler | כל 5 דקות, וגם מיד אחרי כל `helm install` או `helm upgrade` |
| סנכרון אחרי עלייה או restart של ה-daemon | **מיד**, לכל בסיסי הידע |
| סנכרון שוטף | לפי `oikb-interval`. ברירת המחדל היא כל שעה |
| שינוי בתיאור, עד שהתוכן מתעדכן | עד כ-5 דקות, ועוד זמן הסנכרון עצמו |
| push למקור, עד שהתוכן מתעדכן | עד ה-interval הבא. אין webhooks |
| זמן קצוב לבקשה | 60 שניות ל-GitLab, 120 שניות ל-Open WebUI |

### אירועים

| אירוע | מה קורה |
|---|---|
| נוצר בסיס ידע עם שורות `oikb`, ושותף בהרשאת Write | בריצה הבאה הוא נוסף לקונפיגורציה, ה-daemon עולה מחדש, וכל התוכן מועלה |
| לא שותף עם משתמש השירות | המערכת לא רואה אותו, ושום דבר לא קורה |
| שותף בהרשאת קריאה בלבד | מדולג, עם אזהרה בלוג |
| מקור לא נתמך, או שחסר שדה חובה | מדולג, עם אזהרה בלוג |
| שינוי `oikb-branch` | restart. התוכן מתעדכן לענף החדש, ורק קבצים ששונים בין הענפים משתנים |
| שינוי `oikb-path` | restart. התוכן מתחלף לתוכן של התיקייה החדשה |
| שינוי `oikb:` לריפו אחר | restart. התוכן מתחלף כולו |
| החלפת טוקן, או שינוי `oikb-interval` | restart. התוכן לא משתנה |
| שינוי טקסט אחר בתיאור, או שינוי השם | כלום, אפילו לא restart |
| מחיקת שורת `oikb:`, או ביטול השיתוף | הסנכרון מפסיק. **התוכן נשאר** |
| מחיקת בסיס הידע | יוצא מהקונפיגורציה, וה-daemon עולה מחדש |
| לא נשאר אף בסיס ידע לסנכרן | ה-daemon יורד ל-0 רפליקות |
| push למקור, או מחיקת קובץ במקור | ב-interval הבא: הקבצים ששונו מועלים מחדש, והקובץ שנמחק יוצא מבסיס הידע |
| עריכת קובץ בתוך Open WebUI | **לא משוחזר.** כדי להחזיר את הגרסה מהמקור, מוחקים את הקובץ, והסנכרון הבא יעלה אותו מחדש |
| הוספת קובץ ידנית לבסיס ידע מסונכרן | **נמחק** בסנכרון הבא |
| ענף לא קיים, טוקן שפג תוקפו, או מקור לא נגיש | הסנכרון נכשל כבר בשלב הסריקה, **לפני ההשוואה, ולכן שום דבר לא נמחק.** השגיאה מופיעה ב-`/health` ובלוג |
| ה-Secret של הקונפיגורציה נמחק או נערך ידנית | מתוקן אוטומטית בריצה הבאה של ה-reconciler |

---

## הגדרות ה-chart

השדות היחידים שחובה למלא הם `openWebUI.url` ו-`openWebUI.apiKey`, או `existingSecret` במקום המפתח. לכל השאר יש ברירות מחדל עובדות. הערכים נבדקים מול `values.schema.json`, כך שטעות בסוג ערך, או ב-`platform`, בכתובת או ב-interval, מכשילה את ההתקנה עם הודעה ברורה.

| שדה | ברירת מחדל | הסבר |
|---|---|---|
| `openWebUI.url` | – | **חובה.** הכתובת של Open WebUI |
| `openWebUI.apiKey` | – | **חובה, אלא אם יש `existingSecret`.** מפתח ה-API של משתמש השירות |
| `existingSecret` | – | Secret קיים שמכיל את המפתחות |
| `secretKeys.openWebUIApiKey` | `open-webui-api-key` | שם המפתח ב-Secret |
| `secretKeys.daemonApiKey` | `oikb-api-key` | שם המפתח ב-Secret |
| `proxy.httpProxy` / `httpsProxy` / `noProxy` | – | הגדרות proxy |
| `platform` | `auto` | `auto`, `openshift` או `kubernetes` |
| `daemon.image.*` | `ghcr.io/open-webui/oikb`, tag לפי ה-appVersion (`0.6.0`) | חייב להיות 0.5.0 ומעלה |
| `daemon.apiKey` | – | מפתח שמגן על `/sync` ו-`/history`. אם ריק, נוצר אוטומטית ונשמר בין שדרוגים |
| `daemon.logFormat` | `json` | `json` או `text` |
| `daemon.resources` | 100m / 256Mi, limit 1Gi | |
| `daemon.service.port` | `8080` | |
| `daemon.persistence.enabled` | `false` | שמירת היסטוריית הסנכרון ב-PVC. בלי זה ההיסטוריה מתאפסת בכל restart |
| `daemon.metrics.serviceMonitor.enabled` | `false` | ServiceMonitor ל-Prometheus |
| `daemon.extraEnv` | – | משתני סביבה נוספים. **אסור** לשים כאן טוקנים של מקורות |
| `reconciler.image.*` | `python:3.12-alpine` | |
| `reconciler.schedule` | `*/5 * * * *` | תדירות הריצה |
| `reconciler.suspend` | `false` | השהיית ה-CronJob |
| `reconciler.runOnInstall` | `true` | ריצה מיד אחרי התקנה או שדרוג |
| `reconciler.defaultInterval` | `1h` | interval לבסיס ידע שאין לו `oikb-interval` |
| `reconciler.allowAnonymous` | `true` | לאפשר ריפו ציבורי בלי טוקן |
| `reconciler.dryRun` | `false` | רק לכתוב ללוג מה היה נעשה, עם פרטי התחברות מוסתרים |
| `imagePullSecrets`, `nodeSelector`, `tolerations`, `affinity`, `commonLabels` ועוד | – | ראו את המדריך באנגלית |

הרשימה המלאה נמצאת ב-[`charts/oikb-sync/README.md`](../../charts/oikb-sync/README.md#configuration-reference-values).

### האובייקטים שנוצרים

| אובייקט | שם | מי קובע את התוכן |
|---|---|---|
| Secret | `<release>-keys` | Helm |
| Secret | `<release>-config` | **ה-reconciler.** Helm יוצר אותו ריק |
| Deployment ו-Service | `<release>` | Helm. רק את מספר הרפליקות ואת ה-annotation `oikb.openwebui.com/config-hash` קובע **ה-reconciler** |
| ServiceAccount, Role, RoleBinding, ConfigMap, CronJob | `<release>-reconciler` | Helm |

---

## אבטחה

### איפה המידע נשמר

| מידע | איפה | יש בו טוקנים? |
|---|---|---|
| המקור והטוקן, כמו שהמשתמש כתב אותם | ה-DB של Open WebUI, בתיאור של בסיס הידע | **כן** |
| הקונפיגורציה של oikb | Secret `<release>-config`, ב-etcd | **כן** |
| הקונפיגורציה בתוך ה-pod | קובץ בזיכרון (tmpfs) | **כן** |
| מפתחות ה-API | Secret `<release>-keys`, ב-etcd | **כן** |
| היסטוריית הסנכרונים | SQLite ב-`/data` | לא |
| לוגים | stdout | לא. פרטי ההתחברות מוסתרים |

**ל-oikb אין מסד נתונים לקונפיגורציה או לטוקנים.** את ה-Secret של הקונפיגורציה Helm יוצר ריק, ורק ה-reconciler כותב אליו. לכן הטוקנים לא נשמרים בהיסטוריית ה-releases של Helm.

### סיכונים שחשוב להכיר

- **הטוקן בתיאור גלוי לכל מי שיכול לקרוא את בסיס הידע,** כולל משתמשים שהוא משותף איתם ואדמינים. לכן כדאי להשתמש בטוקן לקריאה בלבד, ורק לריפו או למרחב הספציפי, ולא לשתף בסיס ידע מסונכרן עם מי שלא אמור לראות את הטוקן.
- **Secrets מקודדים ב-base64 ולא מוצפנים,** אלא אם הצפנת etcd פעילה. ב-OpenShift אפשר לבדוק עם `oc get apiserver cluster -o jsonpath='{.spec.encryption.type}'`. אם הפלט ריק, ההצפנה כבויה.
- **אסור לתת ל-daemon טוקן גלובלי למקור,** כמו `GITHUB_TOKEN` דרך `daemon.extraEnv`. בסיס ידע בלי טוקן ישתמש בו אוטומטית, וכל משתמש יוכל לסנכרן כל ריפו שהטוקן הזה רואה.
- **משתמש שירות שהוא אדמין** רואה את כל בסיסי הידע בלי שיתוף, אבל המפתח שלו שולט בכל Open WebUI. **מומלץ משתמש רגיל.**

### מה ה-chart עושה

- ל-reconciler יש הרשאות רק על Secret אחד ועל Deployment אחד, לפי שם.
- רק שישה חיבורים נתמכים, ונתיבים מקומיים ו-`web:` נחסמים.
- טוקנים לא מופיעים בלוגים.
- ה-pods רצים בלי root, בלי הרשאות מיוחדות, ועם seccomp.
- `/sync` ו-`/history` של ה-daemon מוגנים במפתח.

---

## תפעול

```bash
NS=oikb; REL=oikb-sync

# להריץ את ה-reconciler עכשיו
kubectl -n $NS create job --from=cronjob/$REL-reconciler $REL-reconcile-now
kubectl -n $NS logs job/$REL-reconcile-now

# מצב ה-daemon
kubectl -n $NS get deploy $REL
kubectl -n $NS logs deploy/$REL

# מצב כל בסיסי הידע, והפעלת סנכרון ידנית
kubectl -n $NS port-forward svc/$REL 8080:8080 &
curl -s localhost:8080/health
KEY=$(kubectl -n $NS get secret $REL-keys -o jsonpath='{.data.oikb-api-key}' | base64 -d)
curl -s -X POST -H "Authorization: Bearer $KEY" localhost:8080/sync/kb-<kb-id>

# הקונפיגורציה שנוצרה (שימו לב: מציג טוקנים!)
kubectl -n $NS get secret $REL-config -o jsonpath='{.data.\.oikb\.yaml}' | base64 -d
```

כל בסיס ידע מופיע בקונפיגורציה בשם `kb-<kb-id>`.

**הרצה יבשה:** עם `--set reconciler.dryRun=true` ה-reconciler רק כותב ללוג את הקונפיגורציה שהיה כותב, עם טוקנים מוסתרים, ולא משנה כלום.

---

## GitOps (Argo CD)

Argo CD מרנדר את ה-chart עם `helm template`, ושם הפונקציה `lookup` לא מחזירה כלום. לכן צריך להגיד ל-Argo לא לגעת במספר הרפליקות וב-annotation שה-reconciler מנהל:

```yaml
spec:
  ignoreDifferences:
    - group: apps
      kind: Deployment
      jsonPointers:
        - /spec/replicas
        - /spec/template/metadata/annotations/oikb.openwebui.com~1config-hash
  syncPolicy:
    syncOptions:
      - RespectIgnoreDifferences=true
```

את המפתחות כדאי לנהל עם `existingSecret` ו-Sealed Secrets או External Secrets, ולא לשמור אותם ב-Git.

---

## תקלות נפוצות

| מה רואים | מה לבדוק |
|---|---|
| `helm install` נכשל על ה-Job | `kubectl -n oikb logs job/<release>-reconciler-init`. בדרך כלל כתובת שגויה, מפתח שגוי, או בעיית רשת או proxy |
| `rejected the API key` | המפתח שגוי; מפתחות API כבויים (`ENABLE_API_KEYS`); למשתמש השירות אין הרשאת *API Keys*; או שהגבלת ה-endpoints חוסמת. ראו [הכנת Open WebUI](#הכנת-open-webui-פעם-אחת-על-ידי-אדמין) |
| בסיס ידע לא מסתנכרן | בלוג ה-reconciler יש שורת `WARN` שמסבירה למה. אם אין שורה כזו, בסיס הידע לא שותף עם משתמש השירות, או שאין בתיאור שורת `oikb:` |
| ה-daemon ב-0 רפליקות | אין עדיין בסיס ידע תקין לסנכרן |
| `Sync failed ...: timeout` | ה-pod לא מגיע למקור. צריך `proxy.*`, או לפתוח חסימת רשת (EgressFirewall או NetworkPolicy) |
| `401`, `403` או `404` מהמקור | טוקן שגוי או שפג תוקפו, חסרה הרשאה (`read_api` ב-GitLab, Contents ב-GitHub), או נתיב או ענף שגויים |
| `rate limit` מ-GitHub | ריפו ציבורי בלי טוקן. צריך להוסיף `oikb-token` |
| שינוי במקור לא הגיע | עוד לא הגיע ה-interval. אפשר להפעיל `/sync/kb-<id>` |
| קובץ שנערך ב-Open WebUI לא חזר לגרסה מהמקור | זו ההתנהגות הצפויה. מוחקים אותו מבסיס הידע |
| ב-OpenShift ה-pod נדחה עם `unable to validate against any security context constraint` | להגדיר `platform: openshift` |
| ב-Kubernetes ה-pod נדחה עם `runAsNonRoot ... non-numeric user` | להגדיר `platform: kubernetes` |

---

## שדרוג והסרה

```bash
helm upgrade oikb-sync ./charts/oikb-sync -n oikb -f my-values.yaml
helm uninstall oikb-sync -n oikb
```

- **שדרוג** שומר את המצב של ה-reconciler: הקונפיגורציה, מספר הרפליקות ומפתח ה-daemon שנוצר. מיד אחרי השדרוג ה-reconciler רץ.
- **לא להשתמש ב-`helm upgrade --force`.** הוא יוצר את האובייקטים מחדש. אם בכל זאת השתמשתם, ה-reconciler יתקן את הקונפיגורציה בריצה הבאה.
- **הסרה** מוחקת את כל מה שה-chart יצר, כולל ה-Secret של הקונפיגורציה. **בסיסי הידע והקבצים ב-Open WebUI לא נפגעים.**
