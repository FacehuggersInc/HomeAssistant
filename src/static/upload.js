const dropZone   = document.getElementById('dropZone');
const fileInput  = document.getElementById('fileInput');
const fileList   = document.getElementById('fileList');
const uploadBtn  = document.getElementById('uploadBtn');
const resultArea = document.getElementById('resultArea');

// Files queued for upload — Map<filename, File>
const queue = new Map();

// ── File type icons ──────────────────────────────────────────────────────────

const ICONS = {
    image: '🖼️',
    zip:   '🗜️',
    video: '🎬',
    audio: '🎵',
    pdf:   '📄',
    text:  '📝',
    other: '📎',
};

function iconFor(filename) {
    const ext = filename.split('.').pop().toLowerCase();
    if (['jpg','jpeg','png','gif','webp','bmp','tiff','svg'].includes(ext)) return ICONS.image;
    if (['zip','tar','gz','bz2','xz','7z','rar'].includes(ext))             return ICONS.zip;
    if (['mp4','mov','avi','mkv','webm'].includes(ext))                     return ICONS.video;
    if (['mp3','wav','flac','ogg','aac'].includes(ext))                     return ICONS.audio;
    if (ext === 'pdf')                                                       return ICONS.pdf;
    if (['txt','md','csv','json','xml'].includes(ext))                      return ICONS.text;
    return ICONS.other;
}

function formatSize(bytes) {
    if (bytes < 1024)        return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// ── Queue management ─────────────────────────────────────────────────────────

function addToQueue(files) {
    for (const file of files) {
        queue.set(file.name, file);
    }
    renderQueue();
}

function renderQueue() {
    fileList.innerHTML = '';

    if (queue.size === 0) {
        fileList.innerHTML = '<p class="empty-hint">No files queued yet.</p>';
        uploadBtn.disabled = true;
        return;
    }

    for (const [name, file] of queue.entries()) {
        const item = document.createElement('div');
        item.className  = 'file-item';
        item.id         = `item-${CSS.escape(name)}`;
        item.innerHTML  = `
            <span class="file-icon">${iconFor(name)}</span>
            <span class="file-name" title="${name}">${name}</span>
            <span class="file-size">${formatSize(file.size)}</span>
        `;
        fileList.appendChild(item);
    }

    uploadBtn.disabled = false;
}

function setItemState(name, state) {
    const item = document.getElementById(`item-${CSS.escape(name)}`);
    if (item) {
        item.className = `file-item ${state}`;
    }
}

// ── Upload ───────────────────────────────────────────────────────────────────

uploadBtn.addEventListener('click', uploadAll);

async function uploadAll() {
    if (queue.size === 0) return;

    uploadBtn.disabled = true;
    resultArea.innerHTML = '';

    const idParam = new URLSearchParams(window.location.search).get('id') || '';

    for (const [name, file] of queue.entries()) {
        setItemState(name, 'uploading');
        try {
            const form = new FormData();
            form.append('file', file, name);

            const res  = await fetch(`${window.location.pathname}?id=${encodeURIComponent(idParam)}`, {
                method: 'POST',
                body:   form,
            });
            const data = await res.json();

            if (res.ok && data.request === 'Success') {
                setItemState(name, 'done');
                addResult(name, 'success', data.message || `${name} uploaded.`);
            } else {
                setItemState(name, 'error');
                addResult(name, 'error', data.reason || `Failed to upload ${name}.`);
            }
        } catch (err) {
            setItemState(name, 'error');
            addResult(name, 'error', `Network error: ${err.message}`);
        }
    }

    queue.clear();
    uploadBtn.disabled = true;
}

function addResult(name, type, msg) {
    const el = document.createElement('div');
    el.className = `result-msg ${type}`;
    el.textContent = msg;
    resultArea.appendChild(el);
}

// ── Drag and drop ────────────────────────────────────────────────────────────

dropZone.addEventListener('dragover', e => {
    e.preventDefault();
    dropZone.classList.add('dragover');
});

dropZone.addEventListener('dragleave', e => {
    if (!dropZone.contains(e.relatedTarget)) {
        dropZone.classList.remove('dragover');
    }
});

dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    addToQueue([...e.dataTransfer.files]);
});

dropZone.addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', () => {
    addToQueue([...fileInput.files]);
    fileInput.value = '';
});