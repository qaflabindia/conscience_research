// app.js - Logic for Conscience Chat
const PERSONA_THRESHOLDS_KEY = 'conscience_persona_thresholds';

const state = {
    apiKey: sessionStorage.getItem('claude_api_key') || '',
    history: [],
    norms: {},
    episodes: [],
    lastScores: {},
    personaThresholds: loadPersonaThresholds()
};

// DOM Elements
const normsContainer = document.getElementById('norms-container');
const episodesContainer = document.getElementById('episodes-container');
const episodesPanel = document.querySelector('.episodes-panel');
const chatMessages = document.getElementById('chat-messages');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const modalOverlay = document.getElementById('modal-overlay');
const apiKeyInput = document.getElementById('api-key-input');
const saveKeyBtn = document.getElementById('save-key-btn');
const settingsBtn = document.getElementById('settings-btn');
const contextPreset = document.getElementById('context-preset');

const PRESETS = {
    'consumer': { harm: 0.70, autonomy: 0.80, honesty: 0.75, privacy: 0.40, fairness: 0.40, confidentiality: 0.40, authority: 0.40 },
    'medical':  { harm: 0.60, autonomy: 0.70, honesty: 0.65, privacy: 0.30, fairness: 0.30, confidentiality: 0.30, authority: 0.30 },
    'children': { harm: 0.50, autonomy: 0.60, honesty: 0.55, privacy: 0.25, fairness: 0.25, confidentiality: 0.25, authority: 0.25 },
    'research': { harm: 0.85, autonomy: 0.90, honesty: 0.85, privacy: 0.55, fairness: 0.55, confidentiality: 0.55, authority: 0.55 }
};

// Initialize
async function init() {
    lucide.createIcons();

    if (state.apiKey) {
        modalOverlay.classList.add('hidden');
    }

    // Initial load only
    await fetchNorms();
    await syncPersonaThresholds();
    fetchEpisodes();
}

function loadPersonaThresholds() {
    try {
        return JSON.parse(localStorage.getItem(PERSONA_THRESHOLDS_KEY) || '{}');
    } catch (err) {
        console.error('Failed to load persona thresholds:', err);
        return {};
    }
}

function storePersonaThreshold(domain, threshold) {
    state.personaThresholds[domain] = threshold;
    localStorage.setItem(PERSONA_THRESHOLDS_KEY, JSON.stringify(state.personaThresholds));
}

async function syncPersonaThresholds() {
    for (const [domain, threshold] of Object.entries(state.personaThresholds)) {
        if (!state.norms[domain]) continue;
        await postThreshold(domain, threshold);
    }
}

// Fetch Norms from Backend
async function fetchNorms() {
    try {
        const response = await fetch('/v1/norms');
        const data = await response.json();
        state.norms = data.norms;
        if (Object.keys(state.personaThresholds).length === 0) {
            Object.entries(state.norms).forEach(([domain, norm]) => {
                state.personaThresholds[domain] = norm.threshold;
            });
            localStorage.setItem(PERSONA_THRESHOLDS_KEY, JSON.stringify(state.personaThresholds));
        } else {
            Object.entries(state.personaThresholds).forEach(([domain, threshold]) => {
                if (state.norms[domain]) {
                    state.norms[domain].threshold = threshold;
                }
            });
        }
        renderNorms();
    } catch (err) {
        console.error('Failed to fetch norms:', err);
    }
}

// Fetch Episodes from Backend
async function fetchEpisodes(options = {}) {
    try {
        const response = await fetch('/v1/episodes');
        const data = await response.json();
        state.episodes = data.episodes
            .slice()
            .sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0))
            .slice(0, 50);
        renderEpisodes();
        if (options.scrollToTop && episodesPanel) {
            episodesPanel.scrollTop = 0;
        }
    } catch (err) {
        console.error('Failed to fetch episodes:', err);
    }
}

function renderNorms() {
    normsContainer.innerHTML = '';
    Object.entries(state.norms).forEach(([domain, norm]) => {
        const percentage = (norm.weight / 3.5) * 100;
        const card = document.createElement('div');
        card.className = 'norm-card';
        card.innerHTML = `
            <div class="norm-header">
                <span class="norm-name" style="text-transform: capitalize;">${domain}</span>
                <span class="norm-weight">w:${norm.weight.toFixed(2)}</span>
            </div>
            <p style="font-size: 0.75rem; color: var(--text-secondary); margin-bottom: 8px;">${norm.rule}</p>
            
            <div class="norm-bar-bg" style="margin-bottom: 12px;">
                <div class="norm-bar-fill" style="width: ${percentage}%"></div>
            </div>

            <div class="sensitivity-bar-container">
                <div id="live-bar-${domain}" class="sensitivity-bar-fill" style="width: 0%"></div>
                <span id="live-val-${domain}" class="sensitivity-value-tag">0.00</span>
            </div>
            
            <div style="margin-bottom: 8px;">
                <div style="display: flex; justify-content: space-between; font-size: 0.7rem; color: var(--text-secondary); margin-bottom: 4px;">
                    <span>Adjust Threshold</span>
                    <span id="val-${domain}" style="color: var(--accent-primary); font-weight: 600;">${norm.threshold.toFixed(2)}</span>
                </div>
                <input type="range" class="threshold-slider" 
                       min="0.00" max="1.00" step="0.01" 
                       value="${norm.threshold}" 
                       oninput="document.getElementById('val-${domain}').innerText = parseFloat(this.value).toFixed(2)"
                       onchange="updateThreshold('${domain}', this.value)">
            </div>

            <div style="font-size: 0.7rem; color: var(--text-secondary); display: flex; justify-content: space-between;">
                <span>Sensitivity</span>
                <span>Violations: ${norm.violations_count}</span>
            </div>
        `;
        normsContainer.appendChild(card);
    });
}

async function updateThreshold(domain, value) {
    const threshold = parseFloat(value);
    storePersonaThreshold(domain, threshold);
    if (state.norms[domain]) {
        state.norms[domain].threshold = threshold;
    }
    try {
        const response = await postThreshold(domain, threshold);
        if (response.ok) {
            renderNorms();
            if (Object.keys(state.lastScores).length > 0) {
                updateLiveMeters(state.lastScores);
            }
        }
    } catch (err) {
        console.error('Failed to update threshold:', err);
    }
}

function postThreshold(domain, threshold) {
    return fetch('/v1/norms/threshold', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain, threshold })
    });
}

function domainListFromAssessment(source) {
    const domains = Object.keys(state.norms || {});
    if (domains.length > 0) return domains;
    if (source?.domain_assessments) return Object.keys(source.domain_assessments);
    if (source?.scores) return Object.keys(source.scores);
    if (source?.thresholds) return Object.keys(source.thresholds);
    return [];
}

function getAssessment(source, domain) {
    const existing = source?.domain_assessments?.[domain];
    if (existing) return existing;

    const score = source?.scores?.[domain] || 0;
    const threshold = source?.thresholds?.[domain] ?? state.norms[domain]?.threshold ?? 0.5;
    const margin = score - threshold;
    return {
        score,
        threshold,
        margin,
        violated: score >= threshold,
        status: score >= threshold ? 'violation' : 'clear'
    };
}

function renderAssessmentTable(source) {
    const rows = domainListFromAssessment(source).map(domain => {
        const assessment = getAssessment(source, domain);
        const score = Number(assessment.score || 0);
        const threshold = Number(assessment.threshold || 0);
        const margin = Number(assessment.margin ?? (score - threshold));
        const isViolated = Boolean(assessment.violated);

        return `
            <tr class="${isViolated ? 'row-violation' : ''}">
                <td style="text-transform: capitalize; font-weight: 500;">${domain}</td>
                <td style="font-family: monospace;">${score.toFixed(3)}</td>
                <td style="font-family: monospace;">${threshold.toFixed(2)}</td>
                <td style="font-family: monospace; color: ${margin >= 0 ? 'var(--error)' : 'var(--text-secondary)'};">${margin.toFixed(3)}</td>
                <td>
                    <span class="${isViolated ? 'status-blocked' : 'status-clear'}">
                        ${isViolated ? 'VIOLATION' : 'CLEAR'}
                    </span>
                </td>
            </tr>
        `;
    }).join('');

    return `
        <div class="details-table-container" style="margin-top: 8px;">
            <table class="details-table">
                <thead>
                    <tr>
                        <th>Domain</th>
                        <th>Score</th>
                        <th>Threshold</th>
                        <th>Delta</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `;
}

function shortDomainName(domain) {
    const names = {
        privacy: 'Pri',
        honesty: 'Hon',
        harm: 'Har',
        autonomy: 'Aut',
        confidentiality: 'Con',
        fairness: 'Fai',
        authority: 'Ath'
    };
    return names[domain] || domain.slice(0, 3);
}

function episodeLabel(ep) {
    const id = ep.episode_id || '';
    if (id.startsWith('chat_user_')) return 'Query';
    if (id.startsWith('chat_claude_')) return 'Answer';
    return 'Episode';
}

function renderEpisodeScores(ep) {
    const domains = domainListFromAssessment(ep);
    if (!domains.length || (!ep.scores && !ep.domain_assessments)) {
        return '';
    }

    const cells = domains.map(domain => {
        const assessment = getAssessment(ep, domain);
        const score = Number(assessment.score || 0);
        const threshold = Number(assessment.threshold || 0);
        const percent = Math.min(100, Math.max(0, score * 100));
        const isViolated = Boolean(assessment.violated);

        return `
            <button class="episode-score-cell ${isViolated ? 'score-violation' : ''}"
                    title="${domain}: score ${score.toFixed(3)} / threshold ${threshold.toFixed(2)}"
                    onclick="openDetailsModal('${ep.episode_id}')">
                <span>${shortDomainName(domain)}</span>
                <strong>${score.toFixed(2)}</strong>
                <i style="width: ${percent}%"></i>
            </button>
        `;
    }).join('');

    return `<div class="episode-score-grid">${cells}</div>`;
}

async function resetServerState() {
    return fetch('/v1/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
    });
}

function updateLiveMeters(scores) {
    state.lastScores = scores;
    Object.entries(scores).forEach(([domain, score]) => {
        const bar = document.getElementById(`live-bar-${domain}`);
        const valTag = document.getElementById(`live-val-${domain}`);
        if (bar && valTag) {
            const percentage = Math.min(100, score * 100);
            bar.style.width = `${percentage}%`;
            valTag.innerText = score.toFixed(2);
            
            // Check against threshold for warning state
            const threshold = state.norms[domain]?.threshold || 0.5;
            if (score >= threshold * 0.8) {
                bar.classList.add('bar-warning');
            } else {
                bar.classList.remove('bar-warning');
            }
        }
    });
}

function renderEpisodes() {
    episodesContainer.innerHTML = '';
    state.episodes.forEach(ep => {
        const isViolation = ep.verdict === 'violation';
        const card = document.createElement('div');
        card.className = 'episode-card';
        card.innerHTML = `
            <div class="episode-header">
                <span class="episode-title" onclick="openDetailsModal('${ep.episode_id}')">
                    ${ep.action}
                </span>
                <div class="episode-actions">
                    <button class="details-icon-btn" onclick="openDetailsModal('${ep.episode_id}')" title="View Sensitivity Profile">
                        <i data-lucide="info" style="width: 14px; height: 14px;"></i>
                    </button>
                    <span class="conscience-tag ${isViolation ? 'tag-blocked' : 'tag-allowed'}" style="font-size: 0.65rem; padding: 1px 6px;">
                        ${ep.verdict.toUpperCase()}
                    </span>
                </div>
            </div>
            <div style="font-size: 0.7rem; color: var(--text-secondary); display: flex; justify-content: space-between;">
                <span>${episodeLabel(ep)} · ${ep.norm_domain}</span>
                <span>Sev: ${(ep.severity || 0).toFixed(2)}</span>
            </div>
            ${renderEpisodeScores(ep)}
        `;
        episodesContainer.appendChild(card);
    });
    lucide.createIcons();
}

function openDetailsModal(episodeId) {
    const ep = state.episodes.find(e => e.episode_id === episodeId);
    if (!ep) return;

    const modal = document.getElementById('details-modal-overlay');
    const tableBody = document.getElementById('details-table-body');
    const subtitle = document.getElementById('details-subtitle');
    
    subtitle.innerText = `Action: ${ep.action} | Status: ${ep.verdict.toUpperCase()}`;
    tableBody.innerHTML = '';

    domainListFromAssessment(ep).forEach(domain => {
        const assessment = getAssessment(ep, domain);
        const tr = document.createElement('tr');
        if (assessment.violated) tr.className = 'row-violation';

        tr.innerHTML = `
            <td style="text-transform: capitalize; font-weight: 500;">${domain}</td>
            <td style="font-family: monospace; font-size: 0.9rem;">${Number(assessment.score || 0).toFixed(3)}</td>
            <td style="font-family: monospace; font-size: 0.9rem; color: var(--text-secondary);">${Number(assessment.threshold || 0).toFixed(2)}</td>
            <td style="font-family: monospace; font-size: 0.9rem; color: var(--text-secondary);">${Number(assessment.margin || 0).toFixed(3)}</td>
            <td>
                <span class="${assessment.violated ? 'status-blocked' : 'status-clear'}">
                    ${assessment.violated ? 'VIOLATION' : 'CLEAR'}
                </span>
            </td>
        `;
        tableBody.appendChild(tr);
    });

    modal.classList.remove('hidden');
    lucide.createIcons();
}

// Handle Chat
async function sendMessage() {
    const text = userInput.value.trim();
    if (!text || !state.apiKey) return;

    userInput.value = '';
    addMessage('user', text);

    const loadingId = addMessage('claude', 'Classifying user intent...', true);

    try {
        const response = await fetch('/v1/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                api_key: state.apiKey,
                message: text,
                history: state.history
            })
        });

        const data = await response.json();
        
        // Remove loading
        document.getElementById(loadingId).remove();

        if (data.error === "Conscience Block") {
            // Blocked user or assistant message
            addMessage('claude', `[CONSCIENCE BLOCK] ${data.message}`, false, data.decision);
            
            if (data.decision && data.decision.scores) {
                updateLiveMeters(data.decision.scores);
            }

            // Even if blocked, update to show the attempt in episodes
            await fetchNorms();
            await fetchEpisodes({ scrollToTop: true });
        } else if (!response.ok) {
            // API or other error
            addMessage('claude', `Error (${response.status}): ${data.message || data.error || 'Unknown error'}`);
        } else if (data.reply) {
            addMessage('claude', data.reply, false, data.claude_decision);
            state.history.push({ role: 'user', content: text });
            state.history.push({ role: 'assistant', content: data.reply });
            
            // Interaction complete, pick up new states
            if (data.claude_decision && data.claude_decision.scores) {
                updateLiveMeters(data.claude_decision.scores);
            }
            await fetchNorms();
            await fetchEpisodes({ scrollToTop: true });
        } else if (data.error) {
            addMessage('claude', `Error: ${data.message || data.error}`);
        }
    } catch (err) {
        document.getElementById(loadingId).remove();
        addMessage('claude', `System Error: ${err.message}`);
    }
}

function addMessage(role, text, isLoading = false, decision = null) {
    const id = 'msg-' + Date.now();
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.id = id;

    let decisionHtml = '';
    if (decision) {
        const isAllowed = decision.allowed;
        decisionHtml = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                <div class="conscience-tag ${isAllowed ? 'tag-allowed' : 'tag-blocked'}">
                    <i data-lucide="${isAllowed ? 'shield-check' : 'shield-alert'}"></i>
                    ${isAllowed ? 'CLEARED' : 'BLOCKED'}: ${decision.norm_domain} (sev: ${decision.severity.toFixed(2)})
                </div>
            </div>
        `;
    }

    const contentHtml = isLoading ? text : marked.parse(text);

    div.innerHTML = `
        ${decisionHtml}
        <div class="message-bubble ${isLoading ? 'loading' : ''}">
            ${contentHtml}
        </div>
    `;
    
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    lucide.createIcons();
    return id;
}

// Event Listeners
sendBtn.addEventListener('click', sendMessage);
userInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendMessage();
});

saveKeyBtn.addEventListener('click', async () => {
    const key = apiKeyInput.value.trim();
    if (key) {
        state.apiKey = key;
        state.history = []; // Clear browser history
        sessionStorage.setItem('claude_api_key', key);
        
        // Clear server-side history only when explicitly starting a new session.
        try {
            await resetServerState();
            await syncPersonaThresholds();
            init(); // Re-fetch to clear sidebars
        } catch (e) {
            console.error('Failed to reset history:', e);
        }
        
        modalOverlay.classList.add('hidden');
    }
});

settingsBtn.addEventListener('click', () => {
    modalOverlay.classList.remove('hidden');
    apiKeyInput.value = state.apiKey;
});

// Helper for modal from chat messages (where we might not have a full episode yet)
window.openDetailsModalFromDecision = function(decision) {
    const modal = document.getElementById('details-modal-overlay');
    const tableBody = document.getElementById('details-table-body');
    const subtitle = document.getElementById('details-subtitle');
    
    subtitle.innerText = `In-Flight Assessment | Result: ${decision.verdict.toUpperCase()}`;
    tableBody.innerHTML = '';

    domainListFromAssessment(decision).forEach(domain => {
        const assessment = getAssessment(decision, domain);
        const tr = document.createElement('tr');
        if (assessment.violated) tr.className = 'row-violation';
        
        tr.innerHTML = `
            <td style="text-transform: capitalize; font-weight: 500;">${domain}</td>
            <td style="font-family: monospace; font-size: 0.9rem;">${Number(assessment.score || 0).toFixed(3)}</td>
            <td style="font-family: monospace; font-size: 0.9rem; color: var(--text-secondary);">${Number(assessment.threshold || 0).toFixed(2)}</td>
            <td style="font-family: monospace; font-size: 0.9rem; color: var(--text-secondary);">${Number(assessment.margin || 0).toFixed(3)}</td>
            <td>
                <span class="${assessment.violated ? 'status-blocked' : 'status-clear'}">
                    ${assessment.violated ? 'VIOLATION' : 'CLEAR'}
                </span>
            </td>
        `;
        tableBody.appendChild(tr);
    });

    modal.classList.remove('hidden');
    lucide.createIcons();
};

window.openDetailsModal = openDetailsModal;

// Deployment Context Presets
contextPreset?.addEventListener('change', (e) => {
    const selected = e.target.value;
    if (PRESETS[selected]) {
        applyPreset(PRESETS[selected]);
    }
});

async function applyPreset(values) {
    for (const [domain, threshold] of Object.entries(values)) {
        // Update local state first
        if (state.norms[domain]) {
            state.norms[domain].threshold = threshold;
            storePersonaThreshold(domain, threshold);
            // Sync to backend
            try {
                await postThreshold(domain, threshold);
            } catch (err) {
                console.error(`Failed to sync ${domain} preset:`, err);
            }
        }
    }
    // Final UI refresh to sync bars and labels
    await fetchNorms();
}

// Start
init();
