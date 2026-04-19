(function () {
  const state = {
    currentView: 'workflow',
    snapshot: null,
    eventCatalog: [],
    searchResults: [],
    selectedSymbolContext: null,
  };

  const elements = {
    refreshSnapshotBtn: document.getElementById('refreshSnapshotBtn'),
    snapshotMeta: document.getElementById('snapshotMeta'),
    snapshotSummary: document.getElementById('snapshotSummary'),
    viewButtons: Array.from(document.querySelectorAll('[data-view]')),
    viewPanels: Array.from(document.querySelectorAll('[data-view-panel]')),
    instrumentSearchForm: document.getElementById('instrumentSearchForm'),
    searchQuery: document.getElementById('searchQuery'),
    searchMarketScope: document.getElementById('searchMarketScope'),
    searchStatus: document.getElementById('searchStatus'),
    instrumentSearchResults: document.getElementById('instrumentSearchResults'),
    selectedInstrumentState: document.getElementById('selectedInstrumentState'),
    selectedTargetRuntime: document.getElementById('selectedTargetRuntime'),
    targetSelectionSummary: document.getElementById('targetSelectionSummary'),
    targetForm: document.getElementById('targetForm'),
    targetId: document.getElementById('targetId'),
    targetSymbol: document.getElementById('targetSymbol'),
    targetMarketScope: document.getElementById('targetMarketScope'),
    targetFormTitle: document.getElementById('targetFormTitle'),
    targetFormHint: document.getElementById('targetFormHint'),
    targetEventTypes: document.getElementById('targetEventTypes'),
    targetEnabled: document.getElementById('targetEnabled'),
    selectAllEventTypesBtn: document.getElementById('selectAllEventTypesBtn'),
    clearEventTypesBtn: document.getElementById('clearEventTypesBtn'),
    resetTargetFormBtn: document.getElementById('resetTargetFormBtn'),
    targetFormMessage: document.getElementById('targetFormMessage'),
    submitTargetBtn: document.getElementById('submitTargetBtn'),
    targetsTable: document.getElementById('targetsTable'),
    eventCatalogTable: document.getElementById('eventCatalogTable'),
    runtimeStatus: document.getElementById('runtimeStatus'),
    storageBindingsTable: document.getElementById('storageBindingsTable'),
  };

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({ error: 'invalid json response' }));
    if (!response.ok) {
      throw new Error(payload.error || payload.detail || `request failed: ${response.status}`);
    }
    return payload;
  }

  function normalizeState(value) {
    return String(value || 'unknown').toLowerCase();
  }

  function isHealthyState(value) {
    return ['running', 'ready', 'ok', 'enabled', 'bound'].includes(normalizeState(value));
  }

  function statusPill(value, label) {
    const normalized = normalizeState(value);
    return `<span class="status-pill ${escapeHtml(normalized)}">${escapeHtml(label || normalized)}</span>`;
  }

  function formatDateTime(value) {
    return value ? new Date(value).toLocaleString() : '미확인';
  }

  function emptyState(message) {
    return `<div class="empty-state">${escapeHtml(message)}</div>`;
  }

  function setFormMessage(message, isError = false) {
    elements.targetFormMessage.textContent = message || '';
    elements.targetFormMessage.style.color = isError ? 'var(--danger)' : 'var(--muted)';
  }

  function setSearchStatus(message, isError = false) {
    elements.searchStatus.textContent = message || '';
    elements.searchStatus.style.color = isError ? 'var(--danger)' : 'var(--muted)';
  }

  function getTargets() {
    return state.snapshot?.collection_targets || [];
  }

  function getTargetStatuses() {
    return Object.fromEntries((state.snapshot?.collection_target_status || []).map((entry) => [entry.target_id, entry]));
  }

  function getRuntimeStatuses() {
    return state.snapshot?.runtime_status || [];
  }

  function getStorageBindings() {
    return state.snapshot?.storage_bindings || [];
  }

  function selectedEventTypes() {
    return Array.from(elements.targetEventTypes.querySelectorAll('input[type="checkbox"]:checked')).map((node) => node.value);
  }

  function findMatchingTarget(symbol, marketScope) {
    const normalizedSymbol = String(symbol || '').trim();
    const normalizedScope = String(marketScope || 'total').trim().toLowerCase();
    return getTargets().find((target) => {
      return (target.instrument?.symbol || '') === normalizedSymbol && (target.market_scope || 'total') === normalizedScope;
    }) || null;
  }

  function getSelectedDraft() {
    const symbol = elements.targetSymbol.value.trim();
    const marketScope = elements.targetMarketScope.value;
    const matchedTarget = findMatchingTarget(symbol, marketScope);
    return {
      symbol,
      marketScope,
      matchedTarget,
      targetId: elements.targetId.value || matchedTarget?.target_id || '',
      name: state.selectedSymbolContext?.symbol === symbol ? state.selectedSymbolContext?.name || '' : '',
    };
  }

  function setView(view) {
    state.currentView = view;
    elements.viewButtons.forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle('is-active', active);
    });
    elements.viewPanels.forEach((panel) => {
      const active = panel.dataset.viewPanel === view;
      panel.hidden = !active;
      panel.classList.toggle('is-active', active);
    });
  }

  function getCounts() {
    const targets = getTargets();
    const runtimeStatuses = getRuntimeStatuses();
    const targetStatuses = Object.values(getTargetStatuses());
    const activeTargetIds = new Set(runtimeStatuses.flatMap((entry) => entry.active_collection_target_ids || []));
    return {
      capturedAt: state.snapshot?.captured_at || null,
      sourceService: state.snapshot?.source_service || 'unknown',
      totalTargets: targets.length,
      enabledTargets: targets.filter((entry) => entry.enabled).length,
      activeTargets: activeTargetIds.size,
      issueCount: targetStatuses.filter((entry) => !isHealthyState(entry.state) || entry.last_error).length,
      eventTypes: state.eventCatalog.length,
      storageBindings: getStorageBindings().length,
    };
  }

  function buildEventTypeCheckboxes(selected = null) {
    const defaultSelection = new Set(state.eventCatalog.map((entry) => entry.event_type));
    const selectedSet = selected || defaultSelection;
    elements.targetEventTypes.innerHTML = state.eventCatalog.map((entry) => `
      <label class="event-option">
        <input type="checkbox" value="${escapeHtml(entry.event_type)}" ${selectedSet.has(entry.event_type) ? 'checked' : ''} />
        <span>
          <span class="event-option-title">${escapeHtml(entry.event_type)}</span>
          <span class="event-option-copy">${escapeHtml(entry.description || '설명 없음')}</span>
        </span>
      </label>
    `).join('');
  }

  function renderSnapshotMeta() {
    if (!state.snapshot) {
      elements.snapshotMeta.textContent = '스냅샷 없음';
      return;
    }
    const counts = getCounts();
    elements.snapshotMeta.textContent = counts.capturedAt
      ? `${counts.sourceService} · ${formatDateTime(counts.capturedAt)}`
      : `${counts.sourceService} · 스냅샷 시간 확인 불가`;
  }

  function renderSnapshotSummary() {
    if (!state.snapshot) {
      elements.snapshotSummary.innerHTML = emptyState('스냅샷을 불러오지 못했습니다.');
      return;
    }

    const counts = getCounts();
    elements.snapshotSummary.innerHTML = `
      <article class="status-card">
        <div class="table-title">수집 대상</div>
        <div class="status-card__value">${escapeHtml(counts.totalTargets)}</div>
        <div class="status-card__meta">활성 ${escapeHtml(counts.enabledTargets)} · 실행중 ${escapeHtml(counts.activeTargets)}</div>
      </article>
      <article class="status-card">
        <div class="table-title">이벤트 종류</div>
        <div class="status-card__value">${escapeHtml(counts.eventTypes)}</div>
        <div class="status-card__meta">설정 가능한 이벤트</div>
      </article>
      <article class="status-card">
        <div class="table-title">확인 필요</div>
        <div class="status-card__value">${escapeHtml(counts.issueCount)}</div>
        <div class="status-card__meta">오류 또는 비정상 상태</div>
      </article>
      <article class="status-card">
        <div class="table-title">스토리지</div>
        <div class="status-card__value">${escapeHtml(counts.storageBindings)}</div>
        <div class="status-card__meta">현재 바인딩 수</div>
      </article>
    `;
  }

  function setSelectedSymbolContext(entry) {
    state.selectedSymbolContext = entry ? {
      symbol: entry.symbol || '',
      marketScope: entry.marketScope || 'total',
      name: entry.name || '',
      source: entry.source || 'manual',
    } : null;
    renderWorkflowContext();
  }

  function renderSelectedSymbolContext() {
    const draft = getSelectedDraft();
    const current = draft.symbol ? {
      symbol: draft.symbol,
      marketScope: draft.marketScope,
      name: draft.name || draft.matchedTarget?.instrument?.name || draft.matchedTarget?.instrument?.display_name || '',
      source: state.selectedSymbolContext?.source || (draft.targetId ? 'existing-target' : 'manual'),
    } : null;

    if (!current?.symbol) {
      elements.selectedInstrumentState.className = 'context-card empty-state';
      elements.selectedInstrumentState.innerHTML = '검색 결과를 선택하거나 종목코드를 직접 입력하면 현재 작업 대상이 여기에 표시됩니다.';
      return;
    }

    elements.selectedInstrumentState.className = 'context-card';
    elements.selectedInstrumentState.innerHTML = `
      <div class="context-card__symbol">
        <strong>${escapeHtml(current.symbol)}</strong>
        ${statusPill(current.marketScope, current.marketScope.toUpperCase())}
      </div>
      <div>${escapeHtml(current.name || '종목명 정보 없음')}</div>
      <div class="context-card__meta">
        <span>${escapeHtml(draft.matchedTarget ? `기존 대상 ${draft.matchedTarget.target_id}` : '새 대상 예정')}</span>
        <span>${escapeHtml(current.source === 'search' ? '검색에서 선택' : current.source === 'existing-target' ? '기존 대상 편집' : '직접 입력')}</span>
      </div>
    `;
  }

  function renderSearchResults() {
    const results = state.searchResults;
    if (!results.length) {
      elements.instrumentSearchResults.innerHTML = emptyState('검색 결과가 없습니다.');
      return;
    }

    elements.instrumentSearchResults.innerHTML = `
      <table class="admin-table">
        <thead>
          <tr>
            <th>종목</th>
            <th>시장 범위</th>
            <th>현재 대상</th>
            <th>작업</th>
          </tr>
        </thead>
        <tbody>
          ${results.map((entry) => {
            const symbol = entry.instrument?.symbol || '';
            const marketScope = entry.market_scope || 'total';
            const matchedTarget = findMatchingTarget(symbol, marketScope);
            return `
              <tr>
                <td>
                  <span class="table-row-title">${escapeHtml(symbol)}</span>
                  <div class="sub">${escapeHtml(entry.display_name || '')}</div>
                </td>
                <td>${statusPill(marketScope, marketScope.toUpperCase())}</td>
                <td>${matchedTarget ? statusPill('enabled', `기존 ${matchedTarget.target_id}`) : statusPill('unknown', '신규')}</td>
                <td>
                  <button
                    type="button"
                    data-action="use-search-result"
                    data-symbol="${escapeHtml(symbol)}"
                    data-market-scope="${escapeHtml(marketScope)}"
                    data-name="${escapeHtml(entry.display_name || '')}"
                  >${matchedTarget ? '확인 후 업데이트' : '이 종목으로 생성'}</button>
                </td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;
  }

  function renderTargetSelectionSummary() {
    const draft = getSelectedDraft();
    const eventTypes = selectedEventTypes();
    if (!draft.symbol) {
      elements.targetSelectionSummary.innerHTML = '<div class="sub">먼저 검색 결과를 선택하거나 종목코드를 입력하세요.</div>';
      return;
    }

    elements.targetSelectionSummary.innerHTML = `
      <div class="summary-list">
        <strong>${escapeHtml(draft.symbol)}</strong>
        ${statusPill(draft.marketScope, draft.marketScope.toUpperCase())}
        ${draft.matchedTarget ? statusPill('enabled', `기존 대상 ${draft.matchedTarget.target_id}`) : statusPill('unknown', '신규 대상')}
      </div>
      <div class="sub">이벤트 ${escapeHtml(eventTypes.length)}개 · ${escapeHtml(elements.targetEnabled.checked ? '활성 저장' : '비활성 저장')}</div>
    `;
  }

  function renderSelectedRuntimeFocus() {
    const draft = getSelectedDraft();
    if (!draft.symbol) {
      elements.selectedTargetRuntime.innerHTML = emptyState('작업할 종목을 선택하면 기존 대상과 런타임 상태가 여기에 정리됩니다.');
      return;
    }

    const runtime = draft.matchedTarget ? getTargetStatuses()[draft.matchedTarget.target_id] || {} : null;
    const runtimeSummary = getRuntimeStatuses()[0] || null;
    const activeTargetIds = new Set(runtimeSummary?.active_collection_target_ids || []);

    elements.selectedTargetRuntime.innerHTML = draft.matchedTarget ? `
      <article class="inspection-card inspection-card--emphasis">
        <div class="panel-kicker">Existing Target</div>
        <div class="inspection-card__headline">
          <strong>${escapeHtml(draft.matchedTarget.target_id)}</strong>
          ${statusPill(draft.matchedTarget.enabled ? 'enabled' : 'disabled', draft.matchedTarget.enabled ? '활성' : '비활성')}
        </div>
        <div class="detail-list">
          <div class="detail-row"><span>이벤트</span><span>${escapeHtml((draft.matchedTarget.event_types || []).join(', ') || '-')}</span></div>
          <div class="detail-row"><span>오너</span><span>${escapeHtml(draft.matchedTarget.owner_service || 'collector')}</span></div>
        </div>
      </article>
      <article class="inspection-card">
        <div class="panel-kicker">Runtime State</div>
        <div class="inspection-card__headline">
          ${statusPill(runtime?.state || 'unknown')}
          ${activeTargetIds.has(draft.matchedTarget.target_id) ? statusPill('running', 'active subscription') : statusPill('stopped', 'not active')}
        </div>
        <div class="detail-list">
          <div class="detail-row"><span>마지막 이벤트</span><span>${escapeHtml(formatDateTime(runtime?.last_event_at))}</span></div>
          <div class="detail-row"><span>마지막 오류</span><span>${escapeHtml(runtime?.last_error || '-')}</span></div>
          <div class="detail-row"><span>확인 시각</span><span>${escapeHtml(formatDateTime(runtime?.observed_at))}</span></div>
        </div>
      </article>
    ` : `
      <article class="inspection-card inspection-card--emphasis">
        <div class="panel-kicker">Existing Target</div>
        <div class="inspection-card__headline">
          <strong>${escapeHtml(draft.symbol)}</strong>
          ${statusPill(draft.marketScope, draft.marketScope.toUpperCase())}
        </div>
        <div>아직 같은 종목 + 시장 범위 조합의 대상이 없습니다. 이 화면에서 저장하면 새 대상이 생성됩니다.</div>
      </article>
      <article class="inspection-card">
        <div class="panel-kicker">Runtime Context</div>
        <div class="detail-list">
          <div class="detail-row"><span>서비스 상태</span><span>${runtimeSummary ? statusPill(runtimeSummary.state || 'unknown') : '미확인'}</span></div>
          <div class="detail-row"><span>실행중 타깃 수</span><span>${escapeHtml(String((runtimeSummary?.active_collection_target_ids || []).length))}</span></div>
          <div class="detail-row"><span>서비스 오류</span><span>${escapeHtml(runtimeSummary?.last_error || '-')}</span></div>
        </div>
      </article>
    `;
  }

  function renderTargetFormState() {
    const draft = getSelectedDraft();
    const isEditing = Boolean(draft.targetId);
    elements.targetFormTitle.textContent = isEditing ? 'Step 3 · Update Existing Target' : 'Step 3 · Create New Target';
    elements.targetFormHint.textContent = isEditing
      ? '이미 존재하는 대상이 있으므로 같은 타깃을 그대로 업데이트합니다.'
      : '선택한 종목 + 시장 범위로 새 수집 대상을 생성합니다.';
    elements.submitTargetBtn.textContent = isEditing ? '업데이트 저장' : '새 대상 저장';
    renderTargetSelectionSummary();
    renderSelectedSymbolContext();
    renderSelectedRuntimeFocus();
  }

  function renderTargets() {
    const targets = getTargets();
    const targetStatuses = getTargetStatuses();
    const draft = getSelectedDraft();

    if (!targets.length) {
      elements.targetsTable.innerHTML = emptyState('등록된 수집 대상이 없습니다.');
      return;
    }

    elements.targetsTable.innerHTML = `
      <table class="admin-table">
        <thead>
          <tr>
            <th>종목</th>
            <th>활성</th>
            <th>이벤트</th>
            <th>런타임</th>
            <th>마지막 오류</th>
            <th>작업</th>
          </tr>
        </thead>
        <tbody>
          ${targets.map((target) => {
            const runtime = targetStatuses[target.target_id] || {};
            const isFocused = draft.symbol && (target.instrument?.symbol || '') === draft.symbol && (target.market_scope || 'total') === draft.marketScope;
            return `
              <tr class="${isFocused ? 'is-focused' : ''}">
                <td>
                  <span class="table-row-title">${escapeHtml(target.instrument?.symbol || '')} · ${escapeHtml((target.market_scope || '').toUpperCase())}</span>
                  <div class="sub">ID ${escapeHtml(target.target_id)}</div>
                </td>
                <td>${statusPill(target.enabled ? 'enabled' : 'disabled', target.enabled ? '활성' : '비활성')}</td>
                <td>${escapeHtml((target.event_types || []).join(', ') || '-')}</td>
                <td>
                  ${statusPill(runtime.state || 'unknown')}
                  <div class="sub">${escapeHtml(formatDateTime(runtime.observed_at))}</div>
                </td>
                <td>${escapeHtml(runtime.last_error || '-')}</td>
                <td>
                  <div class="table-actions">
                    <button type="button" data-action="edit" data-target-id="${escapeHtml(target.target_id)}">편집</button>
                    <button type="button" class="secondary" data-action="disable" data-target-id="${escapeHtml(target.target_id)}">비활성화</button>
                    <button type="button" class="danger" data-action="delete" data-target-id="${escapeHtml(target.target_id)}">삭제</button>
                  </div>
                </td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;
  }

  function renderEventCatalog() {
    if (!state.eventCatalog.length) {
      elements.eventCatalogTable.innerHTML = emptyState('이벤트 카탈로그 정보가 없습니다.');
      return;
    }

    elements.eventCatalogTable.innerHTML = `
      <table class="admin-table">
        <thead>
          <tr>
            <th>이벤트 타입</th>
            <th>토픽</th>
            <th>설명</th>
          </tr>
        </thead>
        <tbody>
          ${state.eventCatalog.map((entry) => `
            <tr>
              <td><span class="table-row-title">${escapeHtml(entry.event_type)}</span></td>
              <td>${escapeHtml(entry.topic_name || '-')}</td>
              <td>${escapeHtml(entry.description || '-')}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  function renderRuntimeStatus() {
    const runtimeStatuses = getRuntimeStatuses();
    if (!runtimeStatuses.length) {
      elements.runtimeStatus.innerHTML = emptyState('런타임 상태 정보가 없습니다.');
      return;
    }

    elements.runtimeStatus.innerHTML = `
      <table class="admin-table">
        <thead>
          <tr>
            <th>컴포넌트</th>
            <th>상태</th>
            <th>실행 타깃</th>
            <th>오류</th>
            <th>확인 시각</th>
          </tr>
        </thead>
        <tbody>
          ${runtimeStatuses.map((entry) => `
            <tr>
              <td><span class="table-row-title">${escapeHtml(entry.component)}</span></td>
              <td>${statusPill(entry.state || 'unknown')}</td>
              <td>${escapeHtml(String((entry.active_collection_target_ids || []).length))}</td>
              <td>${escapeHtml(entry.last_error || '-')}</td>
              <td>${escapeHtml(formatDateTime(entry.observed_at))}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  function renderStorageBindings() {
    const storageBindings = getStorageBindings();
    if (!storageBindings.length) {
      elements.storageBindingsTable.innerHTML = emptyState('노출된 스토리지 바인딩 정보가 없습니다.');
      return;
    }

    elements.storageBindingsTable.innerHTML = `
      <table class="admin-table">
        <thead>
          <tr>
            <th>바인딩 ID</th>
            <th>상태</th>
            <th>저장소</th>
            <th>설명</th>
          </tr>
        </thead>
        <tbody>
          ${storageBindings.map((entry) => `
            <tr>
              <td><span class="table-row-title">${escapeHtml(entry.binding_id || '-')}</span></td>
              <td>${statusPill(entry.state || 'unknown')}</td>
              <td>${escapeHtml(entry.storage_key || entry.storage_type || '-')}</td>
              <td>${escapeHtml(entry.description || '-')}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  function renderWorkflowContext() {
    renderTargetFormState();
    renderTargets();
  }

  function resetTargetForm() {
    elements.targetId.value = '';
    elements.targetSymbol.value = '';
    elements.targetMarketScope.value = 'total';
    elements.targetEnabled.checked = true;
    buildEventTypeCheckboxes();
    setSelectedSymbolContext(null);
    setFormMessage('');
    renderWorkflowContext();
  }

  function populateTargetForm(targetId) {
    const target = getTargets().find((entry) => entry.target_id === targetId);
    if (!target) return;

    elements.targetId.value = target.target_id;
    elements.targetSymbol.value = target.instrument?.symbol || '';
    elements.targetMarketScope.value = target.market_scope || 'total';
    elements.targetEnabled.checked = Boolean(target.enabled);
    buildEventTypeCheckboxes(new Set(target.event_types || []));
    setSelectedSymbolContext({
      symbol: target.instrument?.symbol || '',
      marketScope: target.market_scope || 'total',
      name: target.instrument?.name || target.instrument?.display_name || '',
      source: 'existing-target',
    });
    setFormMessage(`${target.target_id} 편집 중`);
    setView('workflow');
  }

  function syncFormContext(source = 'manual') {
    const symbol = elements.targetSymbol.value.trim();
    const marketScope = elements.targetMarketScope.value;
    if (!symbol) {
      elements.targetId.value = '';
      setSelectedSymbolContext(null);
      return;
    }

    const matchedTarget = findMatchingTarget(symbol, marketScope);
    if (matchedTarget) {
      elements.targetId.value = matchedTarget.target_id;
      elements.targetEnabled.checked = Boolean(matchedTarget.enabled);
      buildEventTypeCheckboxes(new Set(matchedTarget.event_types || []));
    } else {
      buildEventTypeCheckboxes();
    }
    setSelectedSymbolContext({
      symbol,
      marketScope,
      name: state.selectedSymbolContext?.symbol === symbol ? state.selectedSymbolContext.name : '',
      source: matchedTarget ? 'existing-target' : source,
    });
  }

  async function loadSnapshot() {
    const currentTargetId = elements.targetId.value;
    const currentSelectedEvents = new Set(selectedEventTypes());
    const snapshot = await requestJson('/api/admin/snapshot');
    state.snapshot = snapshot;
    state.eventCatalog = snapshot.event_type_catalog || [];

    renderSnapshotMeta();
    renderSnapshotSummary();
    buildEventTypeCheckboxes(currentSelectedEvents.size ? currentSelectedEvents : null);
    renderSearchResults();
    renderTargets();
    renderEventCatalog();
    renderRuntimeStatus();
    renderStorageBindings();

    if (currentTargetId && getTargets().some((entry) => entry.target_id === currentTargetId)) {
      populateTargetForm(currentTargetId);
      return;
    }

    renderWorkflowContext();
  }

  async function handleSearchSubmit(event) {
    event.preventDefault();
    const query = elements.searchQuery.value.trim();
    if (!query) {
      setSearchStatus('검색어를 입력하세요.', true);
      state.searchResults = [];
      renderSearchResults();
      return;
    }

    setSearchStatus('종목 검색 중...');
    elements.instrumentSearchResults.innerHTML = emptyState('검색 결과를 불러오는 중입니다.');
    const result = await requestJson(`/api/admin/instruments?query=${encodeURIComponent(query)}&scope=${encodeURIComponent(elements.searchMarketScope.value)}`);
    state.searchResults = result.instrument_results || [];
    setSearchStatus(state.searchResults.length ? `${state.searchResults.length}건 찾았습니다.` : '검색 결과가 없습니다.');
    renderSearchResults();
  }

  function useSearchResult(dataset) {
    const symbol = dataset.symbol || '';
    const marketScope = dataset.marketScope || 'total';
    const matchedTarget = findMatchingTarget(symbol, marketScope);
    if (matchedTarget) {
      populateTargetForm(matchedTarget.target_id);
      setFormMessage('같은 종목 + 시장 범위의 기존 대상을 불러왔습니다. 여기서 바로 업데이트하세요.');
      return;
    }

    elements.targetId.value = '';
    elements.targetSymbol.value = symbol;
    elements.targetMarketScope.value = marketScope;
    elements.targetEnabled.checked = true;
    buildEventTypeCheckboxes();
    setSelectedSymbolContext({
      symbol,
      marketScope,
      name: dataset.name || '',
      source: 'search',
    });
    setFormMessage('검색한 종목을 작업 영역에 반영했습니다. 이벤트를 선택하고 저장하세요.');
    setView('workflow');
  }

  async function handleTargetSubmit(event) {
    event.preventDefault();
    const draft = getSelectedDraft();
    const payload = {
      target_id: draft.targetId || null,
      symbol: draft.symbol,
      market_scope: draft.marketScope,
      event_types: selectedEventTypes(),
      enabled: elements.targetEnabled.checked,
    };

    if (!payload.symbol) {
      setFormMessage('종목코드를 입력하거나 검색 결과를 선택하세요.', true);
      return;
    }
    if (!payload.event_types.length) {
      setFormMessage('최소 1개 이상의 이벤트를 선택하세요.', true);
      return;
    }

    setFormMessage(payload.target_id ? '기존 수집 대상을 업데이트하는 중입니다...' : '수집 대상을 저장하는 중입니다...');
    const result = await requestJson('/api/admin/targets', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    await loadSnapshot();
    const savedTargetId = result.target?.target_id || result.target_id || payload.target_id;
    if (savedTargetId) {
      populateTargetForm(savedTargetId);
    }
    setFormMessage(result.warning ? `저장되었지만 확인 필요: ${result.warning}` : (payload.target_id ? '기존 수집 대상을 업데이트했습니다.' : '수집 대상을 저장했습니다.'));
  }

  async function disableTarget(targetId) {
    const target = getTargets().find((entry) => entry.target_id === targetId);
    if (!target) return;

    await requestJson('/api/admin/targets', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_id: target.target_id,
        symbol: target.instrument?.symbol,
        market_scope: target.market_scope,
        event_types: target.event_types,
        enabled: false,
      }),
    });
    await loadSnapshot();
    populateTargetForm(targetId);
    setFormMessage(`${targetId} 비활성화 완료`);
  }

  async function deleteTarget(targetId) {
    await requestJson(`/api/admin/targets/${encodeURIComponent(targetId)}`, { method: 'DELETE' });
    if (elements.targetId.value === targetId) {
      resetTargetForm();
    }
    await loadSnapshot();
    setFormMessage(`${targetId} 삭제 완료`);
  }

  elements.viewButtons.forEach((button) => {
    button.addEventListener('click', () => setView(button.dataset.view || 'workflow'));
  });

  elements.refreshSnapshotBtn?.addEventListener('click', () => {
    setFormMessage('스냅샷을 새로고침하는 중입니다...');
    loadSnapshot()
      .then(() => setFormMessage('스냅샷을 새로고침했습니다.'))
      .catch((error) => setFormMessage(error.message, true));
  });

  elements.targetSymbol?.addEventListener('input', () => {
    elements.targetId.value = '';
    syncFormContext('manual');
  });

  elements.targetMarketScope?.addEventListener('change', () => {
    elements.targetId.value = '';
    syncFormContext('manual');
  });

  elements.targetEnabled?.addEventListener('change', renderTargetSelectionSummary);
  elements.targetEventTypes?.addEventListener('change', renderTargetSelectionSummary);

  elements.selectAllEventTypesBtn?.addEventListener('click', () => {
    Array.from(elements.targetEventTypes.querySelectorAll('input[type="checkbox"]')).forEach((node) => {
      node.checked = true;
    });
    renderTargetSelectionSummary();
  });

  elements.clearEventTypesBtn?.addEventListener('click', () => {
    Array.from(elements.targetEventTypes.querySelectorAll('input[type="checkbox"]')).forEach((node) => {
      node.checked = false;
    });
    renderTargetSelectionSummary();
  });

  elements.instrumentSearchForm?.addEventListener('submit', (event) => {
    handleSearchSubmit(event).catch((error) => {
      setSearchStatus(error.message, true);
      setFormMessage(error.message, true);
    });
  });

  elements.targetForm?.addEventListener('submit', (event) => {
    handleTargetSubmit(event).catch((error) => setFormMessage(error.message, true));
  });

  elements.resetTargetFormBtn?.addEventListener('click', resetTargetForm);

  elements.instrumentSearchResults?.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement) || target.dataset.action !== 'use-search-result') return;
    useSearchResult(target.dataset);
  });

  elements.targetsTable?.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const action = target.dataset.action;
    const targetId = target.dataset.targetId;
    if (!action || !targetId) return;

    if (action === 'edit') {
      populateTargetForm(targetId);
      return;
    }
    if (action === 'disable') {
      disableTarget(targetId).catch((error) => setFormMessage(error.message, true));
      return;
    }
    if (action === 'delete') {
      deleteTarget(targetId).catch((error) => setFormMessage(error.message, true));
    }
  });

  setView('workflow');
  renderSnapshotMeta();
  renderSnapshotSummary();
  buildEventTypeCheckboxes();
  renderTargetFormState();
  setSearchStatus('아직 검색하지 않았습니다.');
  elements.instrumentSearchResults.innerHTML = emptyState('검색을 실행하면 결과가 여기에 표시됩니다.');
  elements.targetsTable.innerHTML = emptyState('수집 대상을 불러오는 중입니다.');
  elements.eventCatalogTable.innerHTML = emptyState('이벤트 카탈로그를 불러오는 중입니다.');
  elements.runtimeStatus.innerHTML = emptyState('런타임 상태를 불러오는 중입니다.');
  elements.storageBindingsTable.innerHTML = emptyState('스토리지 바인딩을 불러오는 중입니다.');
  elements.selectedTargetRuntime.innerHTML = emptyState('작업할 종목을 선택하면 기존 대상과 런타임 상태가 여기에 표시됩니다.');

  loadSnapshot().catch((error) => {
    renderSnapshotMeta();
    setFormMessage(error.message, true);
  });
})();
