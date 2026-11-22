// Добавьте раздел с ключом, названием и описанием. Ссылка action нужна для готового раздела.
const sections = [
  {
    id: 'weather',
    observations: true,
    title: 'Метеоданные',
    description: 'Наблюдения станций урочища «Красное». Сравнивайте периоды, изучайте тренды и скачивайте данные.',
    action: { label: 'Открыть метеоаналитику', url: '/meteo#data' },
  },
  {
    id: 'horses',
    title: 'Лошади Пржевальского',
    description: 'Наблюдения и материалы о лошадях Пржевальского.',
  },
  {
    id: 'chronicles',
    title: 'Цифровые летописи природы',
    description: 'Ежегодные материалы наблюдений за природой заповедника.',
  },
];

const previousPages = new Set(['overview', 'tables', 'trends', 'compare', 'stations', 'agro', 'forecast', 'data']);
if (location.pathname === '/' && previousPages.has(location.hash.slice(1).split('?')[0])) {
  location.replace('/meteo' + location.search + location.hash);
}

const navigation = document.querySelector('#sections');
const detail = document.querySelector('#detail');
const launcher = document.querySelector('#launcher');
const launcherTitle = launcher.querySelector('h1');
const listError = document.querySelector('#program-list-error');
const storageKey = 'clgz.launcher.programs';

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function link(text, url, className) {
  const node = element('a', text, className);
  node.href = url;
  return node;
}

function isProgramUrl(input) {
  try {
    const url = new URL(input);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password;
  } catch {
    return false;
  }
}

function savedPrograms() {
  try {
    const entries = JSON.parse(localStorage.getItem(storageKey) || '[]');
    return Array.isArray(entries)
      ? entries.filter(entry => typeof entry?.title === 'string' && isProgramUrl(entry.url))
      : [];
  } catch {
    return [];
  }
}

function renderPrograms() {
  navigation.replaceChildren();
  for (const section of sections) {
    const row = element('div', '', 'program-row');
    const sectionUrl = '#' + section.id;
    row.append(link(section.title, section.action?.url || sectionUrl, 'program-link'));
    navigation.append(row);
  }
  for (const entry of savedPrograms()) {
    const row = element('div', '', 'program-row');
    const remove = element('button', 'Удалить', 'remove-program');
    remove.type = 'button';
    remove.setAttribute('aria-label', 'Удалить «' + entry.title + '»');
    remove.addEventListener('click', () => {
      try {
        const remaining = savedPrograms().filter(program => program.url !== entry.url);
        localStorage.setItem(storageKey, JSON.stringify(remaining));
      } catch {
        listError.textContent = 'Браузер не разрешил удалить программу.';
        listError.hidden = false;
        return;
      }
      listError.hidden = true;
      const neighbor = row.nextElementSibling || row.previousElementSibling;
      row.remove();
      (neighbor?.querySelector('.remove-program') || launcherTitle).focus();
    });
    row.append(link(entry.title, entry.url, 'program-link'), remove);
    navigation.append(row);
  }
}

window.addEventListener('storage', event => {
  if (event.key === storageKey || event.key === null) renderPrograms();
});
renderPrograms();

async function showObservations(panel) {
  const message = element('p', 'Загружаем список файлов…');
  message.setAttribute('role', 'status');
  panel.append(message);
  try {
    const response = await fetch('/api/library/observations');
    if (response.status === 401) {
      message.textContent = 'Исходные наблюдения доступны в кабинете заповедника.';
      panel.append(link('Вход для сотрудника', '/employee', 'button secondary'));
      return;
    }
    if (!response.ok) throw new Error('Список недоступен');
    const files = await response.json();
    if (!files.length) {
      message.textContent = 'Опубликованных файлов пока нет.';
      return;
    }
    const select = element('select');
    const filter = element('label', 'Станция ');
    for (const station of new Set(files.map(file => file.station))) {
      select.add(new Option(station, station));
    }
    filter.append(select);
    const list = element('ul', '', 'files');
    const update = () => {
      const matches = files.filter(file => file.station === select.value).sort((a, b) => b.year - a.year);
      list.replaceChildren(...matches.map(file => {
        const item = element('li');
        item.append(link(file.year + ' · CSV', '/api/library/observations/' + encodeURIComponent(file.file)));
        return item;
      }));
    };
    select.addEventListener('change', update);
    update();
    panel.append(filter, list);
    message.remove();
  } catch {
    message.textContent = 'Не удалось загрузить список. Обновите страницу, чтобы повторить.';
  }
}

function renderPage(moveFocus = false) {
  const section = sections.find(item => location.hash === '#' + item.id);
  launcher.hidden = Boolean(section);
  detail.hidden = !section;
  detail.replaceChildren();
  if (!section) {
    if (moveFocus) launcherTitle.focus();
    return;
  }
  const title = element('h1', section.title);
  title.tabIndex = -1;
  detail.append(link('← Все разделы', '/', 'back'), title, element('p', section.description));
  if (section.observations) {
    detail.append(link(section.action.label, section.action.url, 'button'));
    const panel = element('div', '', 'downloads');
    panel.append(element('h2', 'Исходные наблюдения'),
      element('p', 'Оригинальные файлы кейса по станциям и годам, с флагами качества.'));
    detail.append(panel);
    showObservations(panel);
  } else if (section.action) {
    detail.append(link(section.action.label, section.action.url, 'button'));
  } else {
    detail.append(element('p', 'Материалы пока не опубликованы.', 'notice'));
  }
  if (moveFocus) title.focus();
}

window.addEventListener('hashchange', () => renderPage(true));
renderPage();

fetch('/api/library/session', { cache: 'no-store' })
  .then(response => response.json())
  .then(session => {
    if (session.employee) {
      const employeeLink = document.querySelector('#employee-link');
      employeeLink.href = '/employee/profile';
      employeeLink.textContent = 'Кабинет сотрудника';
    }
  })
  .catch(() => {});
