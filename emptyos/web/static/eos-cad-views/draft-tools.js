import { defineView } from '/static/eos-cad-view.js';
import { mountDraftPane, teardownDraftPane } from '/static/eos-cad-views/draft-bridge.js';

const _v = defineView({
  id: 'draft-tools',
  mount(vctx) { return mountDraftPane(vctx, 'draft-tools'); },
  teardown() { teardownDraftPane('draft-tools'); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
