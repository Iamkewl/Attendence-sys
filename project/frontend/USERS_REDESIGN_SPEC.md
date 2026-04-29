# Users Management Redesign Spec

## Product goal
Build a production-grade Users Management page where admin can create, update, and remove users without leaving the page.

## UX objectives
- Make Add User a first-class flow with a clear modal wizard feel.
- Reduce admin errors with validation and role guidance.
- Keep interaction density high while preserving readability.
- Preserve current dark dashboard visual language.

## Information architecture

### Primary regions
1. Command header
- Title: Users
- Subtitle: Real-time account directory and access control
- Primary action: Add User
- Secondary actions: Refresh, Role filter

2. Insight strip
- Total users
- Active users
- Admin + Instructor count
- Device accounts

3. Data grid
- Columns: Email, Role, Status, Created, Actions
- Row actions: Edit role/status, Delete user
- Empty state with recovery action

4. Modal layer
- Create User modal
- Edit User modal
- Delete confirmation dialog

## Visual direction
- Tone: Surgical ops dashboard, confident and calm.
- Color strategy:
  - Primary: Indigo gradient accents.
  - Support role chips: emerald for active, amber for device, slate for student, indigo for admin.
  - Error: restrained red for destructive actions.
- Depth:
  - Frosted cards + thin border + subtle glow on focus.
- Motion:
  - 180-220ms transitions for hover/focus.
  - Modal enter with short y-axis lift.

## Component spec

### Header block
- Left: page title and user count.
- Right:
  - Role segmented filter: All, Admin, Instructor, Student, Device.
  - Refresh icon button.
  - Add User button with plus icon.

### Stat cards (4)
- Compact cards with icon, value, and label.
- Responsive behavior: 1 column mobile, 2 tablet, 4 desktop.

### Users table
- Sticky header.
- Row hover elevates surface and reveals action buttons.
- Role badge color mapping:
  - admin: primary
  - instructor: accent
  - student: surface
  - device: warning
- Status badge mapping:
  - active: success
  - inactive: danger

### Add User modal
Fields:
- Email (required, email format)
- Password (required, min 8)
- Role (admin, instructor, student, device)
- Active toggle (default true)

Footer actions:
- Cancel
- Create user (loading state)

Validation:
- Inline errors under each invalid field.
- Server error banner at top for conflict or auth issues.

### Edit User modal
Fields:
- Email (optional update)
- Role
- Active toggle

Footer actions:
- Cancel
- Save changes

### Delete confirmation
- Short warning copy.
- Secondary: Cancel.
- Primary destructive: Delete user.
- Disable delete for current signed-in user if known.

## Responsive behavior
- Mobile:
  - Stat cards in 1 column.
  - Table can horizontally scroll.
  - Modals full-width with safe margins.
- Desktop:
  - Full table with all columns visible.

## Accessibility requirements
- Keyboard reachable for all actions.
- Modal focus trap behavior emulated with autofocus + escape close.
- Clear aria labels on icon-only buttons.
- Contrast consistent with WCAG AA.

## API contracts required
- GET /api/v1/users?role=&skip=&limit=
- POST /api/v1/users
- PATCH /api/v1/users/{user_id}
- DELETE /api/v1/users/{user_id}

## State model
- users: array
- loading: boolean
- error: string
- roleFilter: string
- modal states:
  - isCreateOpen
  - editUser
  - deleteUser

## Success criteria
- Add User creates a real database user and updates table instantly.
- Edit User updates role/status/email and refreshes row.
- Delete User removes account and updates counts.
- No mock user data remains in the page.
