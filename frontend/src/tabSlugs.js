// Shared between Sidebar.jsx/App.jsx (navigation) and TickerList.jsx (data
// fetching) so the four tabs, their URL slugs, and their fetch order never
// drift out of sync between the two.
export const TABS = ['Active', 'Inactive', 'Historicals', 'Needs Review']

export const TAB_SLUGS = { Active: 'active', Inactive: 'inactive', Historicals: 'historicals', 'Needs Review': 'needs-review' }
export const SLUGS_TO_TAB = Object.fromEntries(Object.entries(TAB_SLUGS).map(([tab, slug]) => [slug, tab]))
