/*
 * Shared Alpine.js component for the dynamic field/operator/value filter
 * builder used across list-view "Advanced Filter" panels. Lets a user add
 * any number of (field, operator, value) rows instead of a fixed set of
 * per-page dropdowns, remove rows one at a time, and submits as a normal
 * GET form via repeated filter_field / filter_op / filter_value hidden
 * inputs — parsed server-side by core.dynamic_filters.apply_dynamic_filters().
 *
 * Usage:
 *   <div x-data="dynamicFilterBuilder({{ dynamic_fields_json|safe }})"
 *        x-init="initFromServer({{ active_filters_json|default:'[]'|safe }})">
 *     ... see templates/partials/list/_dynamic_filter_builder.html ...
 *   </div>
 *
 * `fields` shape (one entry per filterable field, built by
 * core.dynamic_filters.fields_json()):
 *   { key, label, type: "text"|"choice", operators: [...], choices?: [{value,label}] }
 */
document.addEventListener('alpine:init', () => {
    Alpine.data('dynamicFilterBuilder', (fields = []) => ({
        fields: fields,
        rows: [],
        _nextId: 1,

        initFromServer(activeFilters) {
            if (activeFilters && activeFilters.length) {
                this.rows = activeFilters.map(a => ({
                    id: this._nextId++,
                    field: a.field_key,
                    op: a.op,
                    value: a.value,
                }));
            }
            if (this.rows.length === 0) this.addRow();
        },
        addRow() {
            const first = this.fields[0];
            this.rows.push({
                id: this._nextId++,
                field: '',
                op: first ? (first.operators[0] || 'contains') : 'contains',
                value: '',
            });
        },
        removeRow(idx) {
            this.rows.splice(idx, 1);
            if (this.rows.length === 0) this.addRow();
        },
        onFieldChange(row) {
            const ops = this.operatorsFor(row.field);
            if (!ops.includes(row.op)) row.op = ops[0] || 'contains';
            row.value = '';
        },
        fieldMeta(key) {
            return this.fields.find(f => f.key === key) || null;
        },
        fieldType(key) {
            const f = this.fieldMeta(key);
            return f ? f.type : 'text';
        },
        operatorsFor(key) {
            const f = this.fieldMeta(key);
            return f ? f.operators : ['contains', 'equals', 'is_set', 'is_not_set'];
        },
        choicesFor(key) {
            const f = this.fieldMeta(key);
            return f && f.choices ? f.choices : [];
        },
        opLabel(op) {
            return {
                contains: 'Contains',
                equals: 'Equals',
                is_set: 'Is Set',
                is_not_set: 'Is Not Set',
            }[op] || op;
        },
        needsValue(op) {
            return op === 'contains' || op === 'equals';
        },
    }));
});
