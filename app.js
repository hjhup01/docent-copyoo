const EMPTY_RECORDS = () => ({ docent: [], visit: [] });
const EMPTY_SELECTION = () => ({ docent: null, visit: null });

const state = {
  tab: "docent",
  records: EMPTY_RECORDS(),
  selected: EMPTY_SELECTION(),
  query: "",
  editingId: null,
  // "record" edits exhibition info; "session" adds/edits one docent session
  // of the selected exhibition.
  dialogMode: "record",
  editingSessionIndex: null,
  auth: null,
  authChecked: false,
  loading: false,
  loadError: "",
  saving: false,
};

const elements = {
  tabs: [...document.querySelectorAll(".tab")],
  addButton: document.querySelector("#add-record"),
  list: document.querySelector("#record-list"),
  listTitle: document.querySelector("#list-title"),
  listEyebrow: document.querySelector("#list-eyebrow"),
  resultCount: document.querySelector("#result-count"),
  search: document.querySelector("#search-input"),
  detail: document.querySelector("#detail-panel"),
  dialog: document.querySelector("#record-dialog"),
  form: document.querySelector("#record-form"),
  formContent: document.querySelector("#form-content"),
  submitButton: document.querySelector("#record-form button[type='submit']"),
  dialogTitle: document.querySelector("#dialog-title"),
  dialogEyebrow: document.querySelector("#dialog-eyebrow"),
  authScreen: document.querySelector("#auth-screen"),
  appContent: document.querySelector("#app-content"),
  authMessage: document.querySelector("#auth-message"),
  accountEmail: document.querySelector("#account-email"),
  accountAvatar: document.querySelector("#account-avatar"),
  loginButton: document.querySelector("#login-button"),
  authLoginButton: document.querySelector("#auth-login-button"),
  logoutButton: document.querySelector("#logout-button"),
};

const DOCENT_FIELDS = ["title", "schedule", "space", "detail", "posterUrl"];
const VISIT_FIELDS = ["title", "space", "visitDate", "detail", "evaluation", "impression", "posterUrl"];
const AUDIENCES = [["adults", "성인"], ["teens", "청소년"], ["children", "어린이"]];
const SESSION_FIELDS = ["performedAt", ...AUDIENCES.map(([key]) => key), "questions", "notes", "impression"];

const hasCount = (value) => value !== "" && value != null;

// Session photos: kept in the user's Google Drive, the sheet stores their IDs.
const MAX_PHOTOS = 5;
const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
const photoUrl = (id) => `/api/photos/${encodeURIComponent(id)}`;
// While the session dialog is open: photos already saved, newly picked files, photos to remove.
const photoDraft = { kept: [], added: [], removed: [] };

function resetPhotoDraft(photos = []) {
  photoDraft.added.forEach((item) => URL.revokeObjectURL(item.preview));
  photoDraft.kept = [...photos];
  photoDraft.added = [];
  photoDraft.removed = [];
}

function photoCount() {
  return photoDraft.kept.length + photoDraft.added.length;
}

async function uploadPhoto(file) {
  let response;
  try {
    response = await fetch("/api/photos", {
      method: "POST", credentials: "same-origin", body: file,
      headers: { "Content-Type": file.type || "application/octet-stream" },
    });
  } catch (error) {
    throw new Error("사진을 올리지 못했습니다. 네트워크를 확인해 주세요.", { cause: error });
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result.id) {
    if (response.status === 401) handleSignedOut(result.error);
    throw new Error(result.error || "사진을 올리지 못했습니다.");
  }
  return result.id;
}

// Best effort: photos removed from the journal go to the Drive trash (recoverable for 30 days).
function trashPhotos(ids) {
  ids.forEach((id) => {
    fetch(photoUrl(id), { method: "DELETE", credentials: "same-origin" }).catch(() => {});
  });
}

function sessionPhotosHtml(session) {
  const photos = session.photos || [];
  if (!photos.length) return "";
  return `<div class="session-photos">${photos.map((id, index) => `<a href="${photoUrl(id)}" target="_blank" rel="noopener" title="사진 크게 보기">
    <img src="${photoUrl(id)}" alt="수행 사진 ${index + 1}" loading="lazy" /></a>`).join("")}</div>`;
}

// "총 20명 (성인 12 · 청소년 5 · 어린이 3)"; sessions saved before the
// breakdown existed only have a total.
function participantsText(session) {
  const parts = AUDIENCES.filter(([key]) => hasCount(session[key])).map(([key, label]) => `${label} ${session[key]}명`);
  if (parts.length) {
    const total = AUDIENCES.reduce((sum, [key]) => sum + (Number(session[key]) || 0), 0);
    return `총 ${total}명 (${parts.join(" · ")})`;
  }
  return hasCount(session.participants) ? `${session.participants}명 (구분 없음)` : "미입력";
}

function resetJournal() {
  state.records = EMPTY_RECORDS();
  state.selected = EMPTY_SELECTION();
  state.loadError = "";
}

function updateAuthUI() {
  const loggedIn = Boolean(state.auth);
  // Until the first session check finishes, show neither screen so a logged-in
  // user doesn't see the login card flash on every page load.
  elements.authScreen.hidden = !state.authChecked || loggedIn;
  elements.appContent.hidden = !loggedIn;
  elements.loginButton.hidden = !state.authChecked || loggedIn;
  elements.logoutButton.hidden = !loggedIn;
  elements.accountEmail.textContent = loggedIn ? state.auth.email : "";
  elements.accountAvatar.textContent = loggedIn ? (state.auth.email || "?").charAt(0).toUpperCase() : "";
  document.body.classList.toggle("authenticated", loggedIn);
}

function handleSignedOut(message = "") {
  state.auth = null;
  resetJournal();
  if (elements.dialog.open) elements.dialog.close();
  elements.authMessage.textContent = message;
  updateAuthUI();
}

async function requestApi(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
  } catch (error) {
    throw new Error("앱 서버에 연결할 수 없습니다. MyPJT 서버가 실행 중인지 확인해 주세요.", { cause: error });
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = result.error || "요청을 처리하지 못했습니다.";
    if (response.status === 401) handleSignedOut(message);
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return result;
}

async function loadRecords() {
  let session;
  try {
    session = await requestApi("/api/session");
  } catch (error) {
    console.error("로그인 상태를 확인하지 못했습니다.", error);
    state.authChecked = true;
    handleSignedOut(error.message);
    return;
  }
  state.authChecked = true;
  if (!session.authenticated) {
    state.auth = null;
    updateAuthUI();
    return;
  }

  state.auth = { email: session.email, exhibitionSearch: Boolean(session.exhibitionSearch) };
  state.loading = true;
  state.loadError = "";
  updateAuthUI();
  render();
  try {
    const result = await requestApi("/api/records");
    const records = Array.isArray(result.records) ? result.records : [];
    state.records.docent = records
      .filter((record) => record.kind === "docent")
      .map((record) => ({ ...record, sessions: sortSessions(record.sessions || []) }));
    state.records.visit = records.filter((record) => record.kind === "visit");
    state.selected.docent = state.records.docent[0]?.id || null;
    state.selected.visit = state.records.visit[0]?.id || null;
    elements.authMessage.textContent = "";
  } catch (error) {
    console.error("Google 시트 기록을 불러오지 못했습니다.", error);
    if (error.status === 401) return;
    // Keep the user signed in but block edits: saving replaces the whole sheet,
    // so writing while the journal failed to load would erase existing rows.
    state.loadError = error.message;
  } finally {
    state.loading = false;
  }
  if (state.auth) render();
}

async function saveRecords(records) {
  await requestApi("/api/records", {
    method: "PUT",
    body: JSON.stringify({ records: [...records.docent, ...records.visit] }),
  });
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function parseDate(value) {
  // "YYYY-MM-DD" is parsed as UTC by Date(); treat it as a local calendar day instead.
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (dateOnly) return new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]));
  return new Date(value);
}

function formattedDate(value, options = { year: "numeric", month: "long", day: "numeric" }) {
  if (!value) return "";
  const date = parseDate(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("ko-KR", options).format(date);
}

function currentRecords() {
  const records = state.records[state.tab];
  const query = state.query.trim().toLocaleLowerCase();
  if (!query) return records;
  return records.filter((record) => `${record.title || ""} ${record.space || ""}`.toLocaleLowerCase().includes(query));
}

function selectedRecord() {
  return state.records[state.tab].find((record) => record.id === state.selected[state.tab]);
}

function updateCounts() {
  document.querySelector("#docent-stat").textContent = state.records.docent.length;
  document.querySelector("#visit-stat").textContent = state.records.visit.length;
  document.querySelector("#docent-count").textContent = state.records.docent.length;
  document.querySelector("#visit-count").textContent = state.records.visit.length;
}

function emptyState(icon, title, message, extra = "") {
  return `<div class="empty-state">${icon ? `<div class="empty-icon" aria-hidden="true">${icon}</div>` : ""}<strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p>${extra}</div>`;
}

function renderList() {
  const records = currentRecords();
  elements.listTitle.textContent = state.tab === "docent" ? "Docent 수행 기록" : "관람한 전시 기록";
  elements.listEyebrow.textContent = state.tab === "docent" ? "MY DOCENT JOURNAL" : "MY VISIT JOURNAL";
  elements.addButton.disabled = state.loading || Boolean(state.loadError);

  if (state.loading) {
    elements.resultCount.textContent = "기록을 불러오는 중…";
    elements.list.innerHTML = emptyState("…", "기록을 불러오는 중입니다", "Google 시트에서 기록을 가져오고 있어요.");
    return;
  }
  if (state.loadError) {
    elements.resultCount.textContent = "기록을 불러오지 못했습니다";
    elements.list.innerHTML = emptyState("!", "기록을 불러오지 못했습니다", state.loadError,
      '<button class="button button-quiet retry-load" type="button">다시 시도</button>');
    return;
  }

  elements.resultCount.textContent = `기록 ${records.length}개`;
  if (records.length === 0) {
    const isFiltered = state.query.trim() !== "";
    elements.list.innerHTML = isFiltered
      ? emptyState("⌕", "검색 결과가 없습니다", "다른 제목이나 공간으로 검색해 보세요.")
      : emptyState("＋", "아직 기록이 없습니다", "오른쪽 위의 + 버튼으로 첫 기록을 남겨 보세요.");
    return;
  }

  elements.list.innerHTML = records.map((record) => {
    const space = escapeHtml(record.space || "공간 미입력");
    const subtitle = state.tab === "docent"
      ? `${space} <span class="bullet">·</span> 수행 ${(record.sessions || []).length}회`
      : `${space} <span class="bullet">·</span> ${escapeHtml(formattedDate(record.visitDate, { month: "short", day: "numeric" }) || "방문일 미입력")}`;
    const isSelected = record.id === state.selected[state.tab];
    const poster = safeUrl(record.posterUrl);
    return `<button type="button" class="record-card${isSelected ? " selected" : ""}${poster ? " has-thumb" : ""}" data-id="${escapeHtml(record.id)}" aria-pressed="${isSelected}">
      ${poster ? `<img class="record-thumb" src="${escapeHtml(poster)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : '<span class="record-thumb" aria-hidden="true"></span>'}
      <span class="record-text">
        <h3>${escapeHtml(record.title)}</h3>
        <span class="record-meta">${subtitle}</span>
        ${state.tab === "visit" && record.listened ? '<span class="record-badge">Docent 해설 관람</span>' : ""}
      </span>
    </button>`;
  }).join("");
}

function infoItem(label, value, full = false) {
  if (!value && value !== 0) return "";
  return `<div class="info-item${full ? " full" : ""}"><span class="info-label">${escapeHtml(label)}</span><div class="info-value">${escapeHtml(value)}</div></div>`;
}

function safeUrl(value) {
  const url = String(value || "").trim();
  return /^https?:\/\//i.test(url) ? url : "";
}

function detailTopline(eyebrow) {
  const disabled = state.saving ? " disabled" : "";
  return `<div class="detail-topline">
      <p class="eyebrow">${eyebrow}</p>
      <div class="detail-actions"><button class="text-button edit-record" type="button"${disabled}>수정</button><button class="text-button danger delete-record" type="button"${disabled}>삭제</button></div>
    </div>`;
}

// Title block with the poster next to the key facts (일정·공간·요약).
function detailHero(record, meta) {
  const poster = safeUrl(record.posterUrl);
  return `<header class="detail-hero${poster ? " has-poster" : ""}">
      ${poster ? `<a class="detail-poster" href="${escapeHtml(poster)}" target="_blank" rel="noopener noreferrer" title="포스터 크게 보기">
        <img src="${escapeHtml(poster)}" alt="${escapeHtml(record.title)} 포스터" loading="lazy" referrerpolicy="no-referrer" /></a>` : ""}
      <div class="detail-hero-body">
        <h2 class="detail-title">${escapeHtml(record.title)}</h2>
        <dl class="detail-meta">${meta.map(([icon, label, value, muted]) => `<div>
          <dt><span class="meta-icon" aria-hidden="true">${icon}</span>${escapeHtml(label)}</dt>
          <dd${muted ? ' class="muted"' : ""}>${escapeHtml(value)}</dd></div>`).join("")}</dl>
      </div>
    </header>`;
}

const FACT_LINE = /^(장소|주소|관람료|입장료|문의|누리집|홈페이지|기간|관람\s?시간|작가|참여\s?작가)\s*[:：]\s*(.+)$/;

// Free-text exhibition info: "라벨: 값" lines become a fact table, the rest
// become paragraphs (long text is collapsed behind "더 보기").
function aboutSection(text) {
  const facts = [];
  const paragraphs = String(text || "").replace(/\r/g, "").split(/\n\s*\n/).map((block) => block
    .split("\n")
    .filter((line) => {
      const match = FACT_LINE.exec(line.trim());
      if (match) facts.push([match[1], match[2].trim()]);
      return !match;
    })
    .join("\n")
    .trim()).filter(Boolean);
  const body = paragraphs.length
    ? `<div class="about-text">${paragraphs.map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`).join("")}</div>
       <button type="button" class="link-button about-toggle" hidden>더 보기</button>`
    : "";
  const factList = facts.length
    ? `<dl class="fact-list">${facts.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${
      safeUrl(value) ? `<a href="${escapeHtml(value)}" target="_blank" rel="noopener noreferrer">${escapeHtml(value)}</a>` : escapeHtml(value)
    }</dd></div>`).join("")}</dl>`
    : "";
  return `<section class="detail-section about">
      <div class="section-title"><h3>전시 소개</h3></div>
      ${body || factList ? `${body}${factList}` : '<p class="about-empty">아직 입력된 소개가 없어요. 수정에서 전시 소개나 작품 정보를 적어 보세요.</p>'}
    </section>`;
}

function sortSessions(sessions) {
  return [...sessions].sort((a, b) => String(a.performedAt || "").localeCompare(String(b.performedAt || "")));
}

function sessionParticipants(session) {
  const counts = AUDIENCES.map(([key]) => session[key]).filter(hasCount);
  if (counts.length) return counts.reduce((sum, value) => sum + (Number(value) || 0), 0);
  return hasCount(session.participants) ? Number(session.participants) || 0 : null;
}

// Questions, notes and impressions read top to bottom as one column.
function sessionNotes(session) {
  const notes = [["받은 질문", session.questions], ["특이 사항", session.notes], ["이번 수행의 감상", session.impression]]
    .filter(([, value]) => value);
  if (!notes.length) return "";
  return `<dl class="session-notes">${notes.map(([label, value]) => `<div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>`;
}

function renderDocentDetail(record) {
  // Sessions are stored oldest-first, so the array index is the session number.
  const sessions = (record.sessions || []).map((session, index) => ({ session, index })).reverse();
  const disabled = state.saving ? " disabled" : "";
  const counted = (record.sessions || []).map(sessionParticipants).filter((value) => value !== null);
  const summary = sessions.length
    ? `${sessions.length}회${counted.length ? ` · 누적 ${counted.reduce((a, b) => a + b, 0)}명` : ""}`
    : "아직 없음";
  return `${detailTopline("DOCENT PERFORMANCE")}
    ${detailHero(record, [
      ["◷", "전시 일정", record.schedule || "미입력", !record.schedule],
      ["⌖", "전시 공간", record.space || "미입력", !record.space],
      ["◉", "도슨트 수행", summary, !sessions.length],
    ])}
    ${aboutSection(record.detail)}
    <section class="detail-section">
      <div class="section-title"><h3>수행 회차 <span>총 ${sessions.length}회</span></h3><button class="add-session" type="button"${disabled}>＋ 회차 추가</button></div>
      ${sessions.length ? sessions.map(({ session, index }) => `<article class="session-detail">
        <div class="session-detail-head"><div><strong>${index + 1}회차</strong><span>${escapeHtml(formattedDate(session.performedAt, { year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" }) || "날짜 미입력")}</span></div>
          <div class="detail-actions"><button class="text-button edit-session" type="button" data-index="${index}"${disabled}>수정</button><button class="text-button danger delete-session" type="button" data-index="${index}"${disabled}>삭제</button></div></div>
        <p class="session-people"><span>참여 인원</span>${escapeHtml(participantsText(session))}</p>
        ${sessionNotes(session)}
        ${sessionPhotosHtml(session)}
      </article>`).join("") : emptyState("", "아직 수행 회차가 없습니다", "위의 ＋ 회차 추가 버튼으로 도슨트를 진행한 날의 기록을 남겨 보세요.")}
    </section>`;
}

function renderVisitDetail(record) {
  return `${detailTopline("EXHIBITION VISIT")}
    ${detailHero(record, [
      ["◷", "방문일", formattedDate(record.visitDate) || "미입력", !record.visitDate],
      ["⌖", "전시 공간", record.space || "미입력", !record.space],
      ["◉", "Docent 해설", record.listened ? "들었어요" : "듣지 않았어요", !record.listened],
    ])}
    ${aboutSection(record.detail)}
    ${record.listened ? `<section class="detail-section"><div class="section-title"><h3>Docent 해설 평가</h3></div>
      <div class="visit-note">${escapeHtml(record.evaluation || "아직 평가를 작성하지 않았어요.")}</div>
    </section>` : ""}
    <section class="detail-section"><div class="section-title"><h3>나의 감상</h3></div>
      <div class="visit-note">${escapeHtml(record.impression || "아직 감상을 기록하지 않았어요.")}</div>
    </section>`;
}

function renderDetail() {
  const record = state.loading || state.loadError ? null : selectedRecord();
  if (!record) {
    elements.detail.innerHTML = `<div class="detail-empty">${emptyState("✳", "기록을 선택해 주세요", "왼쪽 목록에서 전시를 선택하면 상세 기록을 볼 수 있습니다.")}</div>`;
    return;
  }
  elements.detail.innerHTML = state.tab === "docent" ? renderDocentDetail(record) : renderVisitDetail(record);
  const about = elements.detail.querySelector(".about-text");
  if (about) {
    about.classList.add("collapsed");
    const overflowing = about.scrollHeight > about.clientHeight + 4;
    about.classList.toggle("collapsed", overflowing);
    elements.detail.querySelector(".about-toggle").hidden = !overflowing;
  }
}

function render() {
  updateCounts();
  elements.tabs.forEach((tab) => {
    const active = tab.dataset.tab === state.tab;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  renderList();
  renderDetail();
}

function renderPhotoGrid() {
  const grid = elements.formContent.querySelector(".photo-grid");
  if (!grid) return;
  const items = [
    ...photoDraft.kept.map((id) => ({ key: `kept:${id}`, src: photoUrl(id) })),
    ...photoDraft.added.map((item, index) => ({ key: `added:${index}`, src: item.preview, isNew: true })),
  ];
  grid.innerHTML = items.length
    ? items.map((item) => `<figure class="photo-thumb"><img src="${escapeHtml(item.src)}" alt="" />
        ${item.isNew ? '<span class="photo-new">새 사진</span>' : ""}
        <button type="button" class="photo-remove" data-key="${escapeHtml(item.key)}" aria-label="사진 빼기">×</button></figure>`).join("")
    : '<p class="photo-empty">아직 사진이 없어요.</p>';
  elements.formContent.querySelector(".photo-count").textContent = `${photoCount()} / ${MAX_PHOTOS}`;
  elements.formContent.querySelector(".photo-pick").classList.toggle("is-disabled", photoCount() >= MAX_PHOTOS);
}

function setupPhotoPicker() {
  const input = elements.formContent.querySelector(".photo-pick input");
  input.addEventListener("change", () => {
    const files = [...input.files];
    input.value = "";
    const room = MAX_PHOTOS - photoCount();
    const accepted = files.filter((file) => /^image\/(jpeg|png|webp)$/.test(file.type) && file.size <= MAX_PHOTO_BYTES);
    if (accepted.length < files.length) window.alert("JPG·PNG·WEBP 사진만, 한 장에 10MB 이하로 올릴 수 있어요.");
    if (accepted.length > room) window.alert(`회차당 사진은 ${MAX_PHOTOS}장까지예요. 앞의 ${Math.max(room, 0)}장만 추가했어요.`);
    accepted.slice(0, Math.max(room, 0)).forEach((file) => {
      photoDraft.added.push({ file, preview: URL.createObjectURL(file) });
    });
    renderPhotoGrid();
  });
  elements.formContent.querySelector(".photo-grid").addEventListener("click", (event) => {
    const button = event.target.closest(".photo-remove");
    if (!button || state.saving) return;
    if (!window.confirm("이 사진을 뺄까요? ‘저장’을 누르면 드라이브 휴지통으로 옮겨져요(30일 동안 되살릴 수 있어요).")) return;
    const [kind, value] = button.dataset.key.split(/:(.*)/s);
    if (kind === "kept") {
      photoDraft.kept = photoDraft.kept.filter((id) => id !== value);
      photoDraft.removed.push(value);
    } else {
      const [removed] = photoDraft.added.splice(Number(value), 1);
      if (removed) URL.revokeObjectURL(removed.preview);
    }
    renderPhotoGrid();
  });
  renderPhotoGrid();
}

function renderSessionForm(record, session = {}) {
  elements.formContent.innerHTML = `<p class="form-section-title">${escapeHtml(record.title)} <span>· 수행 회차</span></p>
    <div class="form-grid">
      <label class="field"><span>수행 날짜와 시간 <b>*</b></span><input name="performedAt" type="datetime-local" required /></label>
      <fieldset class="field full audience-field">
        <legend>참여 인원 <output class="participants-total" aria-live="polite"></output></legend>
        <div class="audience-grid">
          ${AUDIENCES.map(([key, label]) => `<label class="audience"><span>${label}</span><span class="input-suffix"><input name="${key}" type="number" min="0" max="100000" step="1" inputmode="numeric" placeholder="0" /><span>명</span></span></label>`).join("")}
        </div>
        ${!AUDIENCES.some(([key]) => hasCount(session[key])) && hasCount(session.participants)
          ? `<p class="form-hint">이전에 구분 없이 총 ${escapeHtml(session.participants)}명으로 기록된 회차입니다. 구분해서 입력하면 합계가 새로 계산되고, 비워 두면 기존 인원이 유지됩니다.</p>`
          : ""}
      </fieldset>
      <label class="field full"><span>받은 질문</span><textarea name="questions" rows="3" maxlength="20000" placeholder="관람객에게 받은 질문을 기록해 보세요."></textarea></label>
      <label class="field full"><span>특이 사항</span><textarea name="notes" rows="3" maxlength="20000" placeholder="기억에 남는 상황이나 진행 중 참고할 점"></textarea></label>
      <label class="field full"><span>이번 수행의 감상</span><textarea name="impression" rows="4" maxlength="20000" placeholder="오늘 도슨트를 마친 뒤의 생각을 적어 보세요."></textarea></label>
      <div class="field full photo-field">
        <span>수행 사진 <small class="photo-count"></small></span>
        <div class="photo-grid"></div>
        <label class="button button-quiet photo-pick">＋ 사진 추가<input type="file" accept="image/jpeg,image/png,image/webp" multiple hidden /></label>
        <p class="form-hint">JPG·PNG·WEBP, 한 장 10MB 이하, 회차당 ${MAX_PHOTOS}장까지. 저장할 때 긴 변 1600px로 줄이고 위치 정보는 지워 내 구글 드라이브에 올려요.</p>
      </div>
    </div>`;
  SESSION_FIELDS.forEach((name) => { elements.form.elements[name].value = session[name] ?? ""; });
  resetPhotoDraft(session.photos || []);
  setupPhotoPicker();
  const totalOutput = elements.formContent.querySelector(".participants-total");
  const updateTotal = () => {
    const values = AUDIENCES.map(([key]) => elements.form.elements[key].value.trim()).filter(Boolean);
    totalOutput.textContent = values.length ? `합계 ${values.reduce((sum, value) => sum + (Number(value) || 0), 0)}명` : "";
  };
  AUDIENCES.forEach(([key]) => elements.form.elements[key].addEventListener("input", updateTotal));
  updateTotal();
}

// ---- Public exhibition lookup (data.go.kr) -------------------------------

const LOOKUP_DEFAULTS = { status: "running", sort: "latest" };
const lookup = { keyword: "", page: 1, items: [], request: 0, ...LOOKUP_DEFAULTS };
const STATUS_OPTIONS = [["running", "전시중"], ["upcoming", "전시예정"], ["past", "지난전시"], ["all", "전체"]];
const STATUS_LABELS = Object.fromEntries(STATUS_OPTIONS);
const SORT_OPTIONS = [["latest", "최신순"], ["ending", "끝나는 날짜순"], ["title", "이름순"]];

function todayStamp() {
  const now = new Date();
  return `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}`;
}

const STATUS_BADGES = { running: "전시중", upcoming: "전시예정", past: "지난전시" };

function exhibitionStatus(item) {
  let status = item.status;
  if (!STATUS_BADGES[status]) {
    const today = todayStamp();
    status = item.endDate && item.endDate < today ? "past" : item.startDate && item.startDate > today ? "upcoming" : "running";
  }
  return [status, STATUS_BADGES[status]];
}

function compactDate(value) {
  const match = /^(\d{4})(\d{2})(\d{2})$/.exec(String(value || "").trim());
  return match ? `${Number(match[1])}. ${Number(match[2])}. ${Number(match[3])}.` : String(value || "").trim();
}

function exhibitionPeriod(item) {
  const start = compactDate(item.startDate);
  const end = compactDate(item.endDate);
  if (start && end) return start === end ? start : `${start} – ${end}`;
  return start || end;
}

function lookupPanelHtml(isEdit) {
  if (!state.auth?.exhibitionSearch) {
    return `<div class="lookup lookup-off"><p class="lookup-note">공공데이터 전시 검색이 설정되지 않아 <strong>직접 입력</strong>으로 기록합니다.
      <span>.env에 CULTURE_API_KEY를 설정하면 전시를 검색해 선택할 수 있어요.</span></p></div>`;
  }
  const mode = isEdit ? "manual" : "search";
  return `<div class="lookup" data-mode="${mode}">
      <div class="mode-switch" role="group" aria-label="전시 정보 입력 방식">
        <button type="button" class="mode-option" data-mode="search" aria-pressed="${mode === "search"}">⌕ 공공데이터에서 찾기</button>
        <button type="button" class="mode-option" data-mode="manual" aria-pressed="${mode === "manual"}">✎ 직접 입력</button>
      </div>
      <div class="lookup-search">
        <div class="lookup-bar">
          <input type="search" class="lookup-input" maxlength="50" placeholder="전시 제목이나 미술관 이름으로 검색" aria-label="공공데이터 전시 검색어" />
          <button type="button" class="button button-primary lookup-submit">검색</button>
        </div>
        <div class="lookup-filters">
          <div class="period-filter" role="group" aria-label="전시 상태">
            ${STATUS_OPTIONS.map(([value, label]) => `<button type="button" class="period-option" data-status="${value}" aria-pressed="${value === LOOKUP_DEFAULTS.status}">${label}<span class="status-count"></span></button>`).join("")}
          </div>
          <label class="sort-select"><span>정렬</span>
            <select class="lookup-sort" aria-label="검색 결과 정렬">
              ${SORT_OPTIONS.map(([value, label]) => `<option value="${value}"${value === LOOKUP_DEFAULTS.sort ? " selected" : ""}>${label}</option>`).join("")}
            </select>
          </label>
        </div>
        <p class="lookup-status" aria-live="polite">한국문화정보원 공공데이터에서 전시를 찾아 선택하면 아래 정보가 채워져요.</p>
        <ul class="lookup-results" aria-label="전시 검색 결과"></ul>
        <button type="button" class="button button-quiet lookup-more" hidden>결과 더 보기</button>
      </div>
      <p class="lookup-selected" hidden></p>
    </div>`;
}

function setLookupMode(mode, { focus = true } = {}) {
  const panel = elements.formContent.querySelector(".lookup[data-mode]");
  if (!panel) return;
  panel.dataset.mode = mode;
  panel.querySelectorAll(".mode-option").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
  });
  if (!focus) return;
  if (mode === "search") panel.querySelector(".lookup-input").focus();
  else elements.form.elements.title.focus();
}

function setLookupStatus(html) {
  const status = elements.formContent.querySelector(".lookup-status");
  if (status) status.innerHTML = html;
}

const manualButton = '<button type="button" class="link-button lookup-manual">직접 입력하기</button>';

function renderLookupResults() {
  const list = elements.formContent.querySelector(".lookup-results");
  if (!list) return;
  list.innerHTML = lookup.items.map((item, index) => `<li><button type="button" class="lookup-item" data-index="${index}">
      ${item.thumbnail ? `<img src="${escapeHtml(item.thumbnail)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : '<span class="lookup-thumb" aria-hidden="true">▧</span>'}
      <span class="lookup-text"><strong><em class="status-badge status-${exhibitionStatus(item)[0]}">${exhibitionStatus(item)[1]}</em>${escapeHtml(item.title)}</strong>
        <span>${escapeHtml([item.place, item.area].filter(Boolean).join(" · ") || "장소 정보 없음")}</span>
        <span>${escapeHtml(exhibitionPeriod(item) || "기간 정보 없음")}</span></span>
    </button></li>`).join("");
}

async function searchExhibitions(page = 1) {
  const input = elements.formContent.querySelector(".lookup-input");
  const more = elements.formContent.querySelector(".lookup-more");
  const keyword = (page === 1 ? input.value : lookup.keyword).trim();
  if (!keyword) {
    setLookupStatus("검색어를 입력해 주세요.");
    input.focus();
    return;
  }
  const request = ++lookup.request;
  if (page === 1) {
    lookup.items = [];
    renderLookupResults();
    if (keyword !== lookup.keyword) updateStatusCounts();
  }
  lookup.keyword = keyword;
  lookup.page = page;
  more.hidden = true;
  setLookupStatus("공공데이터에서 찾는 중…");
  try {
    const params = new URLSearchParams({ q: keyword, page, status: lookup.status, sort: lookup.sort });
    const result = await requestApi(`/api/exhibitions?${params}`);
    if (request !== lookup.request || !elements.dialog.open) return;
    lookup.items = [...lookup.items, ...(result.items || [])];
    renderLookupResults();
    updateStatusCounts(result.counts);
    more.hidden = !result.hasMore;
    const counts = result.counts || {};
    const truncatedNote = result.truncated ? " 결과가 많아 일부만 보여요. 검색어를 더 구체적으로 입력해 보세요." : "";
    if (!counts.all) {
      setLookupStatus(`‘${escapeHtml(keyword)}’에 해당하는 전시를 공공데이터에서 찾지 못했어요. ${manualButton}`);
    } else if (!lookup.items.length) {
      const others = STATUS_OPTIONS.filter(([value]) => value !== "all" && value !== lookup.status && counts[value])
        .map(([value, label]) => `<button type="button" class="link-button status-jump" data-status="${value}">${label} ${counts[value]}건</button>`);
      setLookupStatus(`‘${escapeHtml(keyword)}’에 ${STATUS_LABELS[lookup.status]}인 전시가 없어요.${others.length ? ` ${others.join(", ")}이 있어요.` : ""} ${manualButton}`);
    } else {
      setLookupStatus(`${STATUS_LABELS[lookup.status]} ${result.total}건 · 선택하면 아래 정보가 채워져요.${truncatedNote} 찾는 전시가 없나요? ${manualButton}`);
    }
  } catch (error) {
    if (request !== lookup.request || error.status === 401) return;
    setLookupStatus(`${escapeHtml(error.message)} ${manualButton}`);
  }
}

function updateStatusCounts(counts = {}) {
  elements.formContent.querySelectorAll(".period-option").forEach((option) => {
    const count = counts[option.dataset.status];
    option.querySelector(".status-count").textContent = count === undefined ? "" : ` ${count}`;
  });
}

function setLookupStatusFilter(status) {
  lookup.status = status;
  elements.formContent.querySelectorAll(".period-option").forEach((option) => {
    option.setAttribute("aria-pressed", String(option.dataset.status === status));
  });
  if (lookup.keyword || elements.formContent.querySelector(".lookup-input").value.trim()) searchExhibitions(1);
}

function fillField(name, value, { onlyIfEmpty = false } = {}) {
  const field = elements.form.elements[name];
  if (!field || !value) return;
  if (onlyIfEmpty && field.value.trim() && field.dataset.autofilled !== "true") return;
  field.value = value;
  field.dataset.autofilled = "true";
}

function detailText(detail) {
  const lines = [];
  if (detail.contents1) lines.push(detail.contents1.length > 3000 ? `${detail.contents1.slice(0, 3000)}…` : detail.contents1);
  const facts = [
    ["장소", detail.placeAddr || [detail.area, detail.sigungu, detail.place].filter(Boolean).join(" ")],
    ["관람료", detail.price],
    ["문의", detail.phone],
    ["누리집", detail.url],
  ].filter(([, value]) => value).map(([label, value]) => `${label}: ${value}`);
  if (facts.length) lines.push(facts.join("\n"));
  return lines.join("\n\n");
}

async function chooseExhibition(item) {
  fillField("title", item.title.slice(0, 120));
  fillField("space", item.place.slice(0, 120));
  if (state.tab === "docent") fillField("schedule", exhibitionPeriod(item).slice(0, 120));
  elements.form.elements.posterUrl.value = item.thumbnail || "";
  updatePosterPreview();
  const selected = elements.formContent.querySelector(".lookup-selected");
  selected.hidden = false;
  selected.innerHTML = `<span class="lookup-badge">공공데이터</span> <strong>${escapeHtml(item.title)}</strong> 정보를 채웠어요. 필요하면 아래에서 고칠 수 있어요.`;
  setLookupMode("manual", { focus: false });
  elements.form.elements.title.focus();
  try {
    const result = await requestApi(`/api/exhibitions/detail?${new URLSearchParams({ seq: item.seq })}`);
    if (!elements.dialog.open || elements.form.elements.title.value !== item.title.slice(0, 120)) return;
    const text = detailText(result.item || {});
    fillField("detail", text.slice(0, 20000), { onlyIfEmpty: true });
    if (result.item?.imgUrl) {
      elements.form.elements.posterUrl.value = result.item.imgUrl;
      updatePosterPreview();
    }
  } catch (error) {
    console.warn("전시 상세 정보를 불러오지 못했습니다.", error);
  }
}

function setupLookup(record) {
  const panel = elements.formContent.querySelector(".lookup[data-mode]");
  if (!panel) return;
  lookup.items = [];
  Object.assign(lookup, LOOKUP_DEFAULTS, { keyword: "" });
  lookup.request += 1;
  const input = panel.querySelector(".lookup-input");
  input.value = record.title || "";
  panel.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.matches(".mode-option")) setLookupMode(button.dataset.mode);
    else if (button.matches(".period-option, .status-jump")) setLookupStatusFilter(button.dataset.status);
    else if (button.matches(".lookup-submit")) searchExhibitions(1);
    else if (button.matches(".lookup-more")) searchExhibitions(lookup.page + 1);
    else if (button.matches(".lookup-manual")) {
      if (!elements.form.elements.title.value.trim()) elements.form.elements.title.value = lookup.keyword;
      setLookupMode("manual");
    } else if (button.matches(".lookup-item")) chooseExhibition(lookup.items[Number(button.dataset.index)]);
  });
  panel.querySelector(".lookup-sort").addEventListener("change", (event) => {
    lookup.sort = event.target.value;
    if (lookup.keyword || input.value.trim()) searchExhibitions(1);
  });
  // Enter in the search box searches instead of submitting the record form.
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      searchExhibitions(1);
    }
  });
  // Hide thumbnails that fail to load ("error" doesn't bubble, so capture it).
  panel.addEventListener("error", (event) => {
    if (event.target.tagName === "IMG") event.target.replaceWith(Object.assign(document.createElement("span"), { className: "lookup-thumb", textContent: "▧" }));
  }, true);
}

function renderForm(record = {}) {
  const lookupHtml = lookupPanelHtml(Boolean(record.id));
  if (state.tab === "docent") {
    elements.formContent.innerHTML = `${lookupHtml}<p class="form-section-title">전시 정보</p>
      <div class="form-grid">
        <label class="field"><span>전시 제목 <b>*</b></span><input name="title" required maxlength="120" placeholder="전시 제목을 입력해 주세요." /></label>
        <label class="field"><span>전시 일정</span><input name="schedule" maxlength="120" placeholder="예: 2026. 9. 1. – 11. 30." /></label>
        <label class="field full"><span>전시 공간</span><input name="space" maxlength="120" placeholder="미술관 또는 전시장 이름" /></label>
        <label class="field full"><span>전시 상세 정보</span><textarea name="detail" rows="4" maxlength="20000" placeholder="전시 소개, 참여 작가, 작품 정보 등을 적어 보세요."></textarea></label>
        <div class="field full poster-field"><label for="poster-url">포스터 이미지</label>
          <div class="poster-input"><div class="poster-preview" aria-hidden="true"></div>
            <div class="poster-controls"><input id="poster-url" name="posterUrl" type="url" maxlength="1000" placeholder="공공데이터에서 전시를 고르면 자동으로 채워져요. 이미지 주소를 직접 넣어도 돼요." />
              <button type="button" class="text-button poster-clear">포스터 빼기</button></div></div></div>
      </div>
      <p class="form-hint">수행 회차는 전시를 저장한 뒤, 목록에서 전시를 선택해 추가할 수 있어요.</p>`;
    DOCENT_FIELDS.forEach((name) => { elements.form.elements[name].value = record[name] || ""; });
  } else {
    elements.formContent.innerHTML = `${lookupHtml}<p class="form-section-title">관람 정보</p>
      <div class="form-grid">
        <label class="field"><span>전시 제목 <b>*</b></span><input name="title" required maxlength="120" placeholder="전시 제목을 입력해 주세요." /></label>
        <label class="field"><span>전시 공간</span><input name="space" maxlength="120" placeholder="미술관 또는 전시장 이름" /></label>
        <label class="field"><span>전시장 방문일</span><input name="visitDate" type="date" /></label>
        <label class="field"><span>Docent 설명</span><span class="checkbox-row"><input name="listened" type="checkbox" /> 설명을 들었어요</span></label>
        <label class="field full"><span>전시 상세 정보</span><textarea name="detail" rows="3" maxlength="20000" placeholder="전시 소개, 참여 작가, 작품 정보 등을 적어 보세요."></textarea></label>
        <div class="field full poster-field"><label for="poster-url">포스터 이미지</label>
          <div class="poster-input"><div class="poster-preview" aria-hidden="true"></div>
            <div class="poster-controls"><input id="poster-url" name="posterUrl" type="url" maxlength="1000" placeholder="공공데이터에서 전시를 고르면 자동으로 채워져요. 이미지 주소를 직접 넣어도 돼요." />
              <button type="button" class="text-button poster-clear">포스터 빼기</button></div></div></div>
        <label class="field full" id="evaluation-field"><span>Docent 설명에 대한 평가</span><textarea name="evaluation" rows="3" maxlength="20000" placeholder="설명에서 좋았던 점이나 인상 깊었던 내용을 적어 보세요."></textarea></label>
        <label class="field full"><span>나의 감상</span><textarea name="impression" rows="4" maxlength="20000" placeholder="작품을 보고 느낀 점과 기억하고 싶은 순간을 적어 보세요."></textarea></label>
      </div>`;
    VISIT_FIELDS.forEach((name) => { elements.form.elements[name].value = record[name] || ""; });
    elements.form.elements.listened.checked = Boolean(record.listened);
    const updateEvaluation = () => {
      const listened = elements.form.elements.listened.checked;
      elements.form.elements.evaluation.disabled = !listened;
      elements.form.querySelector("#evaluation-field").classList.toggle("is-disabled", !listened);
    };
    elements.form.elements.listened.addEventListener("change", updateEvaluation);
    updateEvaluation();
  }
  setupPosterField();
  setupLookup(record);
}

function updatePosterPreview() {
  const preview = elements.formContent.querySelector(".poster-preview");
  if (!preview) return;
  const url = safeUrl(elements.form.elements.posterUrl.value);
  preview.classList.toggle("empty", !url);
  preview.innerHTML = url ? `<img src="${escapeHtml(url)}" alt="" referrerpolicy="no-referrer" />` : "<span>포스터<br />없음</span>";
  preview.querySelector("img")?.addEventListener("error", () => {
    preview.classList.add("empty");
    preview.innerHTML = "<span>불러올 수<br />없어요</span>";
  });
}

function setupPosterField() {
  const input = elements.form.elements.posterUrl;
  if (!input) return;
  input.addEventListener("input", updatePosterPreview);
  elements.formContent.querySelector(".poster-clear").addEventListener("click", () => {
    if (!input.value.trim()) return;
    if (!window.confirm("포스터를 뺄까요? ‘기록 저장’을 누르기 전에는 ‘취소’로 되돌릴 수 있어요.")) return;
    input.value = "";
    updatePosterPreview();
  });
  updatePosterPreview();
}

function openSessionDialog(index = null) {
  const record = selectedRecord();
  if (!record || state.loading || state.loadError || state.saving) return;
  state.dialogMode = "session";
  state.editingId = record.id;
  state.editingSessionIndex = index;
  const session = index === null ? {} : record.sessions?.[index];
  if (!session) return;
  elements.dialogTitle.textContent = index === null ? "수행 회차 추가" : `${index + 1}회차 기록 수정`;
  elements.dialogEyebrow.textContent = "DOCENT SESSION";
  renderSessionForm(record, session);
  setSaving(false);
  elements.dialog.showModal();
  elements.formContent.scrollTop = 0;
  elements.formContent.querySelector("input[name='performedAt']").focus();
}

function openDialog(record = null) {
  if (state.loading || state.loadError || state.saving) return;
  state.dialogMode = "record";
  state.editingSessionIndex = null;
  state.editingId = record?.id || null;
  elements.dialogTitle.textContent = record ? "전시 기록 수정" : (state.tab === "docent" ? "Docent 기록 추가" : "관람 기록 추가");
  elements.dialogEyebrow.textContent = record ? "EDIT EXHIBITION" : "NEW EXHIBITION";
  renderForm(record || {});
  setSaving(false);
  elements.dialog.showModal();
  elements.formContent.scrollTop = 0;
  const searchFirst = elements.formContent.querySelector(".lookup[data-mode='search'] .lookup-input");
  (searchFirst || elements.formContent.querySelector("input[name='title']")).focus();
}

function fieldValue(container, name) {
  const field = container.querySelector(`[name="${name}"]`);
  return field && !field.disabled ? field.value.trim() : "";
}

function collectSessionData(previous = {}) {
  const session = {};
  SESSION_FIELDS.forEach((name) => { session[name] = fieldValue(elements.formContent, name); });
  const counts = AUDIENCES.map(([key]) => key);
  counts.forEach((key) => { session[key] = session[key] === "" ? "" : Number(session[key]); });
  const filled = counts.filter((key) => session[key] !== "");
  // With no breakdown entered, keep a total recorded before the breakdown existed.
  session.participants = filled.length
    ? filled.reduce((sum, key) => sum + session[key], 0)
    : (previous.participants ?? "");
  return session;
}

// Only the fields of the current form are collected; docent sessions are
// managed separately from the exhibition info and kept as they are.
function collectFormData() {
  const form = elements.formContent;
  if (state.tab === "docent") {
    const record = { kind: "docent" };
    DOCENT_FIELDS.forEach((name) => { record[name] = fieldValue(form, name); });
    return record;
  }
  const record = { kind: "visit" };
  VISIT_FIELDS.forEach((name) => { record[name] = fieldValue(form, name); });
  record.listened = elements.form.elements.listened.checked;
  if (!record.listened) record.evaluation = "";
  return record;
}

function setSaving(saving) {
  state.saving = saving;
  elements.submitButton.disabled = saving;
  elements.submitButton.textContent = saving ? "저장 중…" : "기록 저장";
  elements.form.setAttribute("aria-busy", String(saving));
}

// Every change is sent as the full journal, so build the next state first and
// only commit it locally once Google Sheets accepted it.
async function commitRecords(nextRecords) {
  setSaving(true);
  try {
    await saveRecords(nextRecords);
    state.records = nextRecords;
    return true;
  } catch (error) {
    console.error("Google 시트에 전시 기록을 저장하지 못했습니다.", error);
    if (error.status !== 401) window.alert(error.message);
    return false;
  } finally {
    setSaving(false);
  }
}

async function saveSession() {
  const tab = state.tab;
  const records = [...state.records[tab]];
  const index = records.findIndex((item) => item.id === state.editingId);
  if (index < 0) {
    window.alert("회차를 추가할 전시를 찾을 수 없습니다. 페이지를 새로고침해 주세요.");
    return;
  }
  const sessions = [...(records[index].sessions || [])];
  const session = collectSessionData(state.editingSessionIndex === null ? {} : sessions[state.editingSessionIndex]);
  const uploaded = [];
  if (photoDraft.added.length) {
    setSaving(true);
    elements.submitButton.textContent = "사진 올리는 중…";
    try {
      for (const item of photoDraft.added) uploaded.push(await uploadPhoto(item.file));
    } catch (error) {
      trashPhotos(uploaded);
      setSaving(false);
      if (state.auth) window.alert(error.message);
      return;
    }
  }
  session.photos = [...photoDraft.kept, ...uploaded];
  if (state.editingSessionIndex === null) sessions.push(session);
  else sessions[state.editingSessionIndex] = session;
  records[index] = { ...records[index], sessions: sortSessions(sessions) };
  if (!await commitRecords({ ...state.records, [tab]: records })) {
    trashPhotos(uploaded);
    return;
  }
  trashPhotos(photoDraft.removed);
  resetPhotoDraft();
  state.editingId = null;
  state.editingSessionIndex = null;
  elements.dialog.close();
  render();
}

async function deleteSession(sessionIndex) {
  const record = selectedRecord();
  const session = record?.sessions?.[sessionIndex];
  if (state.saving || !session) return;
  const when = formattedDate(session.performedAt, { year: "numeric", month: "long", day: "numeric" }) || "날짜 미입력";
  const photoNote = (session.photos || []).length ? ` 이 회차의 사진 ${session.photos.length}장은 드라이브 휴지통으로 옮겨져요.` : "";
  if (!window.confirm(`${sessionIndex + 1}회차(${when}) 기록을 삭제할까요? 삭제한 기록은 복구할 수 없습니다.${photoNote}`)) return;
  const tab = state.tab;
  const records = state.records[tab].map((item) => (item.id === record.id
    ? { ...item, sessions: item.sessions.filter((_, index) => index !== sessionIndex) }
    : item));
  if (!await commitRecords({ ...state.records, [tab]: records })) return;
  trashPhotos(session.photos || []);
  render();
}

async function saveForm(event) {
  event.preventDefault();
  if (state.saving || !elements.form.reportValidity()) return;
  if (state.dialogMode === "session") {
    await saveSession();
    return;
  }
  const tab = state.tab;
  const record = collectFormData();
  const records = [...state.records[tab]];
  if (state.editingId) {
    const index = records.findIndex((item) => item.id === state.editingId);
    if (index < 0) {
      window.alert("수정하려는 기록을 찾을 수 없습니다. 페이지를 새로고침해 주세요.");
      return;
    }
    record.id = state.editingId;
    records[index] = { ...records[index], ...record };
  } else {
    record.id = crypto.randomUUID();
    if (tab === "docent") record.sessions = [];
    records.unshift(record);
  }
  if (!await commitRecords({ ...state.records, [tab]: records })) return;
  state.selected[tab] = record.id;
  state.editingId = null;
  elements.dialog.close();
  render();
}

async function deleteSelected() {
  const record = selectedRecord();
  const photos = (record?.sessions || []).flatMap((session) => session.photos || []);
  const photoNote = photos.length ? ` 수행 사진 ${photos.length}장은 드라이브 휴지통으로 옮겨져요.` : "";
  if (state.saving || !record || !window.confirm(`"${record.title}" 기록을 삭제할까요? 삭제한 기록은 복구할 수 없습니다.${photoNote}`)) return;
  const tab = state.tab;
  const remaining = state.records[tab].filter((item) => item.id !== record.id);
  if (!await commitRecords({ ...state.records, [tab]: remaining })) return;
  trashPhotos(photos);
  state.selected[tab] = remaining[0]?.id || null;
  render();
}

function selectTab(tabName, focus = false) {
  state.tab = tabName;
  state.query = "";
  elements.search.value = "";
  if (!selectedRecord()) state.selected[state.tab] = state.records[state.tab][0]?.id || null;
  render();
  if (focus) elements.tabs.find((tab) => tab.dataset.tab === tabName)?.focus();
}

elements.tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectTab(tab.dataset.tab));
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const last = elements.tabs.length - 1;
    const next = event.key === "Home" ? 0
      : event.key === "End" ? last
        : (index + (event.key === "ArrowRight" ? 1 : -1) + elements.tabs.length) % elements.tabs.length;
    selectTab(elements.tabs[next].dataset.tab, true);
  });
});

function startGoogleLogin() {
  window.location.assign("/auth/google");
}

elements.loginButton.addEventListener("click", startGoogleLogin);
elements.authLoginButton.addEventListener("click", startGoogleLogin);
elements.logoutButton.addEventListener("click", async () => {
  try {
    await requestApi("/api/logout", { method: "POST" });
    handleSignedOut();
  } catch (error) {
    console.error("Google 로그아웃을 완료하지 못했습니다.", error);
    window.alert(error.message);
  }
});
elements.addButton.addEventListener("click", () => openDialog());
elements.search.addEventListener("input", (event) => {
  state.query = event.target.value;
  renderList();
});
elements.list.addEventListener("click", (event) => {
  if (event.target.closest(".retry-load")) {
    loadRecords();
    return;
  }
  const card = event.target.closest(".record-card");
  if (!card) return;
  state.selected[state.tab] = card.dataset.id;
  render();
  elements.list.querySelector(`.record-card[data-id="${CSS.escape(card.dataset.id)}"]`)?.focus();
});
elements.detail.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || button.disabled) return;
  if (button.matches(".edit-record")) openDialog(selectedRecord());
  else if (button.matches(".delete-record")) deleteSelected();
  else if (button.matches(".add-session")) openSessionDialog();
  else if (button.matches(".edit-session")) openSessionDialog(Number(button.dataset.index));
  else if (button.matches(".delete-session")) deleteSession(Number(button.dataset.index));
  else if (button.matches(".about-toggle")) {
    const about = elements.detail.querySelector(".about-text");
    const collapsed = about.classList.toggle("collapsed");
    button.textContent = collapsed ? "더 보기" : "접기";
  }
});
// Posters are external images; if one fails to load, drop it instead of
// showing a broken image ("error" doesn't bubble, so listen in capture phase).
elements.detail.addEventListener("error", (event) => {
  if (event.target.tagName !== "IMG") return;
  event.target.closest(".detail-hero")?.classList.remove("has-poster");
  event.target.closest(".detail-poster")?.remove();
}, true);
elements.list.addEventListener("error", (event) => {
  if (event.target.tagName !== "IMG") return;
  event.target.closest(".record-card")?.classList.remove("has-thumb");
  event.target.replaceWith(Object.assign(document.createElement("span"), { className: "record-thumb" }));
}, true);
elements.form.addEventListener("submit", saveForm);
document.querySelectorAll(".close-dialog, .cancel-dialog").forEach((button) => {
  button.addEventListener("click", () => {
    if (!state.saving) elements.dialog.close();
  });
});
elements.dialog.addEventListener("click", (event) => {
  if (event.target === elements.dialog && !state.saving) elements.dialog.close();
});
elements.dialog.addEventListener("cancel", (event) => {
  // Don't let Esc close the dialog while a save is in flight.
  if (state.saving) event.preventDefault();
});
document.addEventListener("keydown", (event) => {
  const active = document.activeElement;
  const typing = active && (["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName) || active.isContentEditable);
  if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey
    && state.auth && !elements.dialog.open && !typing) {
    event.preventDefault();
    elements.search.focus();
  }
});

const loginErrors = {
  cancelled: "로그인이 취소되었습니다. Google 계정으로 다시 로그인해 주세요.",
  state: "로그인 요청이 만료되었거나 확인되지 않았습니다. 다시 로그인해 주세요.",
};
const loginError = new URLSearchParams(window.location.search).get("login_error");
if (loginError) {
  elements.authMessage.textContent = loginErrors[loginError]
    || "Google 로그인에 실패했습니다. OAuth 설정과 승인된 리디렉션 URI를 확인해 주세요.";
  window.history.replaceState({}, "", window.location.pathname);
}
updateAuthUI();
loadRecords();
