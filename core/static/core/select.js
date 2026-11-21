/* D121 GoTani select: a design-system dropdown in place of the browser's native list.
   - Progressive enhancement: every single <select> is wrapped in <div class="app-select" x-data="gtSelect">; the real
     <select> stays in the form (visually hidden), so submit, Django validation, x-model, onchange and data-show-when
     keep working. The custom button and list only read its options and set its value + fire input/change.
   - Pill button like .app-field, rounded list (radius-lg, shadow-float) teleported to <body> so cards, tables and
     sheets never clip it; hover = surface, selected = green-100 with a check. A search box appears above 8 options.
   - Keyboard (WAI-ARIA select-only combobox): ↓/↑/Enter/Space/Alt+↓ open, ↓/↑/Home/End/PageUp/PageDown move,
     Enter/Space pick, Esc closes (without closing the sheet), Tab closes, typing jumps to the first match.
   - Left native: phones and tablets (coarse pointer, their OS picker is better), <select multiple>, the date picker's
     month list and anything marked data-native.
   Loaded with defer before Alpine, so the wrappers exist when Alpine starts; <template> contents (x-for rows) are
   enhanced too, so cloned rows carry the component. */
(function () {
  var T = function (s, a) { return (window.gtT || function (x) { return x; })(s, a); };
  var SEARCH_AT = 8, uid = 0;
  var touch = window.matchMedia && matchMedia('(hover: none) and (pointer: coarse)').matches;
  var CHEV = '<svg class="gt-ico app-select__chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
  var CHECK = '<svg class="gt-ico app-select__check" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>';
  var SEARCH = '<svg class="gt-ico" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>';

  var MARKUP =
    '<button type="button" role="combobox" aria-haspopup="listbox" :id="id + \'-btn\'" :aria-controls="id + \'-list\'"' +
    ' :aria-expanded="open ? \'true\' : \'false\'" :aria-label="name" :aria-describedby="describedby" :aria-invalid="invalid ? \'true\' : null"' +
    ' :aria-activedescendant="open && !searchable && active >= 0 && list[active] ? id + \'-o\' + list[active].i : null"' +
    ' :disabled="disabled" :class="{ \'is-open\': open }" class="app-select__btn" @click="toggle()" @keydown="key($event)">' +
    '<span class="app-select__value" :class="{ \'is-placeholder\': empty }" x-text="label"></span>' + CHEV + '</button>' +
    '<template x-teleport="body"><div class="app-select__pop no-print" :id="id + \'-pop\'" x-show="open" x-cloak :class="{ \'is-up\': up }"' +
    '>' +
    '<label class="app-select__search" x-show="searchable">' + SEARCH +
    '<input type="text" x-model="q" @input="active = firstEnabled(0, 1)" @keydown="key($event)" autocomplete="off" spellcheck="false"' +
    ' role="combobox" aria-autocomplete="list" :aria-controls="id + \'-list\'" aria-expanded="true" :aria-label="searchLabel"' +
    ' :aria-activedescendant="active >= 0 && list[active] ? id + \'-o\' + list[active].i : null" :placeholder="searchLabel"></label>' +
    '<ul class="app-select__list" role="listbox" :id="id + \'-list\'" :aria-label="name" @mousedown.prevent>' +
    '<template x-for="(o, k) in list" :key="o.i"><li role="option" :id="id + \'-o\' + o.i" class="app-select__opt"' +
    ' :class="{ \'is-active\': k === active, \'is-selected\': o.i === index, \'is-empty\': o.value === \'\' }"' +
    ' :aria-selected="o.i === index ? \'true\' : \'false\'" :aria-disabled="o.disabled ? \'true\' : null"' +
    ' @click="pick(o)" @mousemove="if (!o.disabled) active = k"><span class="app-select__text" x-text="o.text"></span>' + CHECK + '</li></template>' +
    '<li class="app-select__none" x-show="!list.length" x-text="noneLabel"></li></ul></div></template>';

  // The field's label without the options of a select nested inside it or the required star.
  function labelText(sel) {
    var lab = sel.labels && sel.labels[0];
    if (!lab) return '';
    var c = lab.cloneNode(true);
    c.querySelectorAll('select, .app-select, .gt-tip, .app-req').forEach(function (n) { n.remove(); });
    return c.textContent.replace(/\s+/g, ' ').trim();
  }
  function skip(sel) {
    return touch || sel.multiple || sel.size > 1 || sel.hasAttribute('data-native') ||
      sel.classList.contains('flatpickr-monthDropdown-months') || (sel.parentNode && sel.parentNode.classList &&
      sel.parentNode.classList.contains('app-select')) || (sel.closest && sel.closest('[data-native-selects]'));
  }
  function enhance(sel) {
    if (skip(sel)) return;
    var wrap = (sel.ownerDocument || document).createElement('div');
    var cls = ['app-select'];
    ['app-field--sm', 'app-field--auto', 'app-grow'].forEach(function (c) { if (sel.classList.contains(c)) cls.push(c.replace('app-field', 'app-select')); });
    if (sel.classList.contains('app-field--unit')) cls.push('app-select--unit');
    wrap.className = cls.join(' ');
    wrap.setAttribute('x-data', 'gtSelect');
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    wrap.insertAdjacentHTML('beforeend', MARKUP);
    var btn = wrap.querySelector('.app-select__btn');
    // The button looks like the field it replaces (size modifiers included).
    btn.className += ' ' + Array.prototype.filter.call(sel.classList, function (c) { return /^app-field/.test(c); }).join(' ');
    if (btn.className.indexOf('app-field') < 0) btn.className += ' app-field';
  }
  function scan(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('select').forEach(enhance);
    root.querySelectorAll('template').forEach(function (t) { if (!t.hasAttribute('x-teleport')) scan(t.content); });
  }
  window.gtSelectScan = scan;
  scan(document);
  // Selects added later (outside Alpine templates) are wrapped as they arrive; Alpine initialises the new x-data.
  new MutationObserver(function (ms) {
    ms.forEach(function (m) {
      m.addedNodes.forEach(function (n) {
        if (n.nodeType !== 1) return;
        if (n.tagName === 'SELECT') enhance(n); else if (n.querySelector && n.querySelector('select')) scan(n);
      });
    });
  }).observe(document.documentElement, { childList: true, subtree: true });

  document.addEventListener('alpine:init', function () {
    Alpine.data('gtSelect', function () {
      var sel, btn, typed = '', typedAt = 0;  // the <select> stays out of Alpine state (no proxy around a DOM node)
      return {
        id: 'gts' + (++uid), open: false, up: false, q: '', active: -1, opts: [], index: -1,
        label: '', empty: true, disabled: false, required: false, prompt: false, invalid: false, name: '', describedby: null,
        searchLabel: T('Cari…'), noneLabel: T('Tidak ada yang cocok'),
        get searchable() { return this.opts.length > SEARCH_AT; },
        get list() {
          var q = this.q.trim().toLowerCase(), req = this.required || this.prompt;
          return this.opts.filter(function (o) {
            if (o.value === '' && req) return false;   // a prompt ("Pilih siklus…") is not a choice
            return !q || o.text.toLowerCase().indexOf(q) >= 0;
          });
        },
        init() {
          sel = this.$el.querySelector('select');
          btn = this.$el.querySelector('.app-select__btn');  // DOM lookups, not $refs: these run from plain listeners too
          sel.classList.add('app-select__native');
          sel.tabIndex = -1;
          sel.setAttribute('aria-hidden', 'true');
          var self = this;
          sel.addEventListener('focus', function () { btn.focus(); });
          sel.addEventListener('change', function () { self.sync(); });
          new MutationObserver(function () { self.sync(); }).observe(sel, {
            childList: true, subtree: true, characterData: true, attributes: true,
            attributeFilter: ['disabled', 'selected', 'required', 'aria-invalid', 'aria-describedby', 'value', 'label'] });
          this.sync();
          this.$nextTick(function () {
            // x-model sets the value from code without an event; follow it reactively.
            if (sel._x_model && window.Alpine) Alpine.effect(function () { sel._x_model.get(); self.$nextTick(function () { self.sync(); }); });
            self.sync();
          });
          if (sel.form) sel.form.addEventListener('reset', function () { setTimeout(function () { self.sync(); }); });
          this._reposition = function () { if (self.open) self.place(); };
        },
        sync() {
          var o = [];
          Array.prototype.forEach.call(sel.options, function (op, i) {
            o.push({ i: i, value: op.value, text: (op.label || op.text || '').trim(), disabled: op.disabled });
          });
          this.opts = o;
          this.index = sel.selectedIndex;
          var cur = sel.selectedIndex >= 0 ? o[sel.selectedIndex] : null;
          this.empty = !cur || cur.value === '';
          this.label = (cur && cur.text) || sel.getAttribute('data-placeholder') || T('Pilih…');
          this.disabled = sel.disabled; this.required = sel.required; this.prompt = sel.hasAttribute('data-prompt');
          this.invalid = sel.getAttribute('aria-invalid') === 'true';
          this.describedby = sel.getAttribute('aria-describedby');
          this.name = sel.getAttribute('aria-label') || labelText(sel);
        },
        firstEnabled(from, dir) {
          var l = this.list;
          for (var k = from; k >= 0 && k < l.length; k += dir) if (!l[k].disabled) return k;
          return -1;
        },
        toggle() { if (this.open) this.close(true); else this.show(); },
        show(at) {
          if (this.disabled) return;
          this.sync();
          this.q = '';
          var l = this.list, cur = this.index;
          var k = l.findIndex(function (o) { return o.i === cur; });
          this.active = at === 'first' ? this.firstEnabled(0, 1) : at === 'last' ? this.firstEnabled(l.length - 1, -1) : (k >= 0 ? k : this.firstEnabled(0, 1));
          this.open = true;
          var self = this;
          this.$nextTick(function () {
            self.place();
            var q = self.searchInput();
            if (self.searchable && q) q.focus();
            self.scrollActive();
          });
          window.addEventListener('scroll', this._reposition, true);
          window.addEventListener('resize', this._reposition);
          this._outside = function (e) {
            var pop = document.getElementById(self.id + '-pop');
            if (self.$el.contains(e.target) || (pop && pop.contains(e.target))) return;
            self.close(false);
          };
          document.addEventListener('pointerdown', this._outside, true);
        },
        close(focus) {
          if (!this.open) return;
          this.open = false;
          window.removeEventListener('scroll', this._reposition, true);
          window.removeEventListener('resize', this._reposition);
          document.removeEventListener('pointerdown', this._outside, true);
          if (focus) btn.focus();
        },
        place() {
          var pop = document.getElementById(this.id + '-pop');
          if (!pop) return;
          var r = btn.getBoundingClientRect(), vh = window.innerHeight, vw = document.documentElement.clientWidth;
          // As wide as the field (long options wrap); a narrow field (page size, unit) still gets a readable list.
          pop.style.width = Math.min(Math.max(r.width, 224), vw - 16) + 'px';
          var h = pop.offsetHeight, below = vh - r.bottom - 8, above = r.top - 8;
          this.up = h > below && above > below;
          pop.style.top = (this.up ? Math.max(8, r.top - h - 6) : r.bottom + 6) + 'px';
          var w = pop.offsetWidth, left = r.left + w > vw - 8 ? r.right - w : r.left;  // keep the edge that fits
          pop.style.left = Math.max(8, Math.min(left, vw - w - 8)) + 'px';
          // Off screen (the field scrolled away inside a sheet): close rather than float over something else.
          if (r.bottom < 0 || r.top > vh) this.close(false);
        },
        searchInput() { var pop = document.getElementById(this.id + '-pop'); return pop && pop.querySelector('input'); },
        scrollActive() {
          var l = this.list[this.active], el = l && document.getElementById(this.id + '-o' + l.i);
          if (el) el.scrollIntoView({ block: 'nearest' });
        },
        move(to) {
          var l = this.list;
          if (!l.length) return;
          to = Math.max(0, Math.min(l.length - 1, to));
          var dir = to >= this.active ? 1 : -1, k = this.firstEnabled(to, dir);
          if (k < 0) k = this.firstEnabled(to, -dir);
          if (k >= 0) { this.active = k; var self = this; this.$nextTick(function () { self.scrollActive(); }); }
        },
        pick(o) {
          if (!o || o.disabled) return;
          var changed = sel.selectedIndex !== o.i;
          sel.selectedIndex = o.i;
          this.sync();
          this.close(true);
          if (changed) {
            sel.dispatchEvent(new Event('input', { bubbles: true }));
            sel.dispatchEvent(new Event('change', { bubbles: true }));
          }
        },
        typeahead(ch) {
          var now = Date.now();
          typed = now - typedAt > 700 ? ch : typed + ch;
          typedAt = now;
          var t = typed.toLowerCase(), l = this.list;
          var k = l.findIndex(function (o) { return !o.disabled && o.text.toLowerCase().indexOf(t) === 0; });
          if (k >= 0) { this.active = k; var self = this; this.$nextTick(function () { self.scrollActive(); }); }
        },
        key(e) {
          var k = e.key, inSearch = !!(e.target.closest && e.target.closest('.app-select__search'));
          if (!this.open) {
            if (k === 'ArrowDown' || k === 'ArrowUp' || k === 'Enter' || k === ' ') { e.preventDefault(); this.show(); }
            else if (k === 'Home') { e.preventDefault(); this.show('first'); }
            else if (k === 'End') { e.preventDefault(); this.show('last'); }
            else if (k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
              e.preventDefault();
              this.show();
              if (this.searchable) { this.q = k; this.active = this.firstEnabled(0, 1); } else this.typeahead(k);
            }
            return;
          }
          if (k === 'Escape') { e.preventDefault(); e.stopPropagation(); this.close(true); }
          else if (k === 'Tab') { this.close(false); }
          else if (k === 'ArrowDown') { e.preventDefault(); if (e.altKey) return; this.move(this.active + 1); }
          else if (k === 'ArrowUp') { e.preventDefault(); if (e.altKey) { this.close(true); return; } this.move(this.active - 1); }
          else if (k === 'PageDown') { e.preventDefault(); this.move(this.active + 10); }
          else if (k === 'PageUp') { e.preventDefault(); this.move(this.active - 10); }
          else if ((k === 'Home' || k === 'End') && !inSearch) { e.preventDefault(); this.move(k === 'Home' ? 0 : this.list.length - 1); }
          else if (k === 'Enter' || (k === ' ' && !inSearch)) { e.preventDefault(); this.pick(this.list[this.active]); }
          else if (!inSearch && k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); this.typeahead(k); }
        },
      };
    });
  });
})();
