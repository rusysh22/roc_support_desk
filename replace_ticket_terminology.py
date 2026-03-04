import os, re

replacements = {
    'Ticket Number': 'Ticket Number',
    'ticket number': 'ticket number',
    'Ticket Submitted': 'Ticket Submitted',
    'ticket submitted': 'ticket submitted',
    'Submit a Ticket': 'Submit a Ticket',
    'submit a ticket': 'submit a ticket',
    'Ticket Category': 'Ticket Category',
    'Ticket Categories': 'Ticket Categories',
    'Create Ticket': 'Create Ticket',
    'create ticket': 'create ticket',
    'Search tickets': 'Search tickets',
    'Search Tickets': 'Search Tickets',
    'New Ticket': 'New Ticket',
    'Open Tickets': 'Open Tickets',
    'My Tickets': 'My Tickets',
    'Recent Tickets': 'Recent Tickets',
    'Ticket Details': 'Ticket Details',
    'Service Ticket': 'Service Ticket',
    'Assigned Ticket': 'Assigned Ticket',
    'Assigned Tickets': 'Assigned Tickets',
    'Ticket Overview': 'Ticket Overview',
    'Unassigned Tickets': 'Unassigned Tickets',
    'Total Tickets': 'Total Tickets',
    'Resolved Tickets': 'Resolved Tickets',
    'Escalate Ticket': 'Escalate Ticket',
    'Escalate ticket': 'Escalate ticket',
    'Update Ticket': 'Update Ticket',
    'Delete Ticket': 'Delete Ticket',
    'Edit Ticket': 'Edit Ticket',
    'Ticket Actions': 'Ticket Actions',
    'Ticket Assignment': 'Ticket Assignment',
    'Ticket Timeline': 'Ticket Timeline',
    'Ticket Data': 'Ticket Data',
    'Ticket Record': 'Ticket Record',
    'ticket record': 'ticket record',
    'Escalated Tickets': 'Escalated Tickets',
    'ticket assignment': 'ticket assignment',
    'Ticket Escalation': 'Ticket Escalation',
}

files_to_check = []
for root, _, files in os.walk('.'):
    if 'venv' in root or '__pycache__' in root or '.git' in root or 'node_modules' in root:
        continue
    for f in files:
        if f.endswith('.html') or f.endswith('.py'):
            files_to_check.append(os.path.join(root, f))

changed_files = 0
for filepath in files_to_check:
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    original_content = content
    for old, new in replacements.items():
        content = content.replace(old, new)
        
    content = content.replace('>Ticket<', '>Ticket<')
    content = content.replace(' Ticket ', ' Ticket ')
    content = content.replace(' Tickets<', ' Tickets<')
    content = content.replace(' Tickets ', ' Tickets ')
    content = content.replace('"Ticket"', '"Ticket"')
    content = content.replace('"Tickets"', '"Tickets"')
    content = content.replace('Tickets:', 'Tickets:')
    
    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        changed_files += 1

print(f'Successfully updated {changed_files} files.')
