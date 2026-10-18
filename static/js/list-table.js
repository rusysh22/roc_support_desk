/*
 * Shared Alpine.js component for the standardized list-view tables
 * (checkbox select-all/one, row-click-to-open, and the confirm-modal
 * state the bulk-action bar dispatches into). Generalized from the
 * inline x-data in partials/case_results.html and the toggleAll/toggleOne
 * helpers in templates/desk/users/list.html.
 *
 * Usage on the table wrapper:
 *   <div x-data="jkListTable([{{ row ids as JS array }}])"> ... </div>
 * Each row:
 *   <tr class="cursor-pointer hover:bg-slate-50" @click="openRow('{{ url }}')">
 *     <td @click.stop><input type="checkbox" x-model="selectedIds" value="{{ obj.pk }}"></td>
 *     ...
 *   </tr>
 */
document.addEventListener('alpine:init', () => {
    Alpine.data('jkListTable', (rowIds = []) => ({
        selectedIds: [],
        rowIds: rowIds,
        confirmOpen: false,
        confirmTitle: '',
        confirmMessage: '',
        confirmSubmit: () => {},

        get allSelected() {
            return this.rowIds.length > 0 && this.selectedIds.length === this.rowIds.length;
        },
        get canDuplicate() {
            return this.selectedIds.length === 1;
        },
        toggleAll() {
            this.selectedIds = this.allSelected ? [] : [...this.rowIds];
        },
        openRow(url) {
            if (url) window.location.href = url;
        },
    }));
});
