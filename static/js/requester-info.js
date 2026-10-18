/*
 * "Your Information" block on the new-ticket form (templates/client/create_case.html).
 *
 * - Full Name searches the employee directory; picking someone fills unit and
 *   job role and posts the Employee id (contacts come back masked, the server
 *   reads the real ones).
 * - Which contacts are asked for follows the selected category's
 *   "Requester Contact" setting: either / email / phone / both.
 * - Company Unit and Job Role are searchable dropdowns over master data.
 */
function requesterInfo() {
    const readJson = (id, fallback) => {
        const el = document.getElementById(id);
        if (!el) return fallback;
        try { return JSON.parse(el.textContent); } catch (e) { return fallback; }
    };
    const initial = readJson('requester-initial-data', {});
    const contactMap = readJson('category-contact-data', {});

    return {
        searchUrl: initial.searchUrl || '',
        picked: initial.pickedId ? { id: initial.pickedId, name: initial.name, unit_name: '', job_role: initial.jobRole,
                                     has_email: false, has_phone: false, email_masked: '', phone_masked: '' } : null,
        nameQuery: initial.pickedId ? '' : (initial.name || ''),
        results: [],
        nameOpen: false,
        loading: false,
        email: initial.email || '',
        phone: initial.phone || '',
        contactMode: initial.contactMode === 'phone' ? 'phone' : 'email',
        requirement: 'either',
        suggestion: '',
        unitOptions: readJson('unit-options-data', []),
        unitId: initial.unitId || '',
        unitSearch: '',
        unitOpen: false,
        roleOptions: readJson('job-role-options-data', []),
        jobRole: initial.jobRole || '',
        roleSearch: initial.jobRole || '',
        roleOpen: false,
        _searchSeq: 0,

        init() {
            const unit = this.unitOptions.find(o => o.id === this.unitId);
            if (unit) this.unitSearch = unit.name;
            const cat = document.getElementById('id_category');
            this.requirement = this.requirementFor(cat ? cat.value : '');
            if (cat) cat.addEventListener('change', () => { this.requirement = this.requirementFor(cat.value); });
        },

        requirementFor(categoryId) {
            return contactMap[categoryId] || 'either';
        },

        // --- Which contact inputs to show -------------------------------
        get needsEmail() { return this.requirement === 'email' || this.requirement === 'both'; },
        get needsPhone() { return this.requirement === 'phone' || this.requirement === 'both'; },
        get showToggle() {
            if (this.requirement !== 'either') return false;
            return !this.picked || (!this.picked.has_email && !this.picked.has_phone);
        },
        get showEmail() { return this.needsEmail && !(this.picked && this.picked.has_email); },
        get showPhone() { return this.needsPhone && !(this.picked && this.picked.has_phone); },
        // Only the visible inputs are posted, so a hidden stale value never sneaks in.
        get sentEmail() {
            if (this.showEmail) return this.email;
            if (this.showToggle && this.contactMode === 'email') return this.email;
            return '';
        },
        get sentPhone() {
            if (this.showPhone) return this.phone;
            if (this.showToggle && this.contactMode === 'phone') return this.phone;
            return '';
        },

        // --- Employee directory search -----------------------------------
        async search() {
            const q = this.nameQuery.trim();
            if (q.length < 2) { this.results = []; this.nameOpen = false; return; }
            const seq = ++this._searchSeq;
            this.loading = true;
            this.nameOpen = true;
            try {
                const res = await fetch(`${this.searchUrl}?q=${encodeURIComponent(q)}`);
                const data = await res.json();
                if (seq === this._searchSeq) this.results = data.results || [];
            } catch (e) {
                if (seq === this._searchSeq) this.results = [];
            } finally {
                if (seq === this._searchSeq) this.loading = false;
            }
        },

        pick(r) {
            this.picked = r;
            this.nameOpen = false;
            this.results = [];
            this.email = '';
            this.phone = '';
            if (r.unit_id) {
                const unit = this.unitOptions.find(o => o.id === r.unit_id);
                this.unitId = r.unit_id;
                this.unitSearch = unit ? unit.name : r.unit_name;
            }
            if (r.job_role && (!this.roleOptions.length || this.roleOptions.includes(r.job_role))) {
                this.jobRole = r.job_role;
                this.roleSearch = r.job_role;
            }
        },

        clearPicked() {
            this.nameQuery = this.picked ? this.picked.name : '';
            this.picked = null;
            this.$nextTick(() => document.getElementById('requester-name-search')?.focus());
        },

        // --- Dropdowns -------------------------------------------------------
        get filteredUnits() {
            const q = this.unitSearch.trim().toLowerCase();
            const selected = this.unitOptions.find(o => o.id === this.unitId);
            if (!q || (selected && selected.name.toLowerCase() === q)) return this.unitOptions;
            return this.unitOptions.filter(o => o.name.toLowerCase().includes(q));
        },
        selectUnit(opt) {
            this.unitId = opt.id;
            this.unitSearch = opt.name;
            this.unitOpen = false;
        },
        get filteredRoles() {
            const q = this.roleSearch.trim().toLowerCase();
            if (!q || q === this.jobRole.toLowerCase()) return this.roleOptions;
            return this.roleOptions.filter(o => o.toLowerCase().includes(q));
        },
        selectRole(opt) {
            this.jobRole = opt;
            this.roleSearch = opt;
            this.roleOpen = false;
        },

        // --- Email typo hint -------------------------------------------------
        checkEmail(value) {
            this.suggestion = '';
            if (!value || typeof Mailcheck === 'undefined') return;
            const that = this;
            Mailcheck.run({
                email: value,
                // 'id' (Indonesia ccTLD) isn't in Mailcheck's default list, so
                // e.g. 'company.co.id' would be 'corrected' to 'co.il'.
                topLevelDomains: Mailcheck.defaultTopLevelDomains.concat(['co.id', 'ac.id', 'go.id', 'or.id', 'net.id', 'web.id', 'id']),
                suggested(s) { that.suggestion = s.full; },
                empty() { that.suggestion = ''; },
            });
        },
    };
}
