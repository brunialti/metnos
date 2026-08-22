/* Metnos documentation shell: navigation is deliberately client-side so the
 * public documents remain the single, clean corpus compiled by the Tutor. */
(() => {
  "use strict";
  if (!document.body || document.body.dataset.wikiReady) return;

  const paths = {
    home: { it: "/it/", en: "/en/" },
    webui: { it: "/it/webui.html", en: "/en/webui.html" },
    interface: { it: "/it/interface.html", en: "/en/interface.html" },
    domains: { it: "/it/domains.html", en: "/en/domains.html" },
    quick_tour: { it: "/it/Metnos_QuickTour.html", en: "/en/Metnos_QuickTour.html" },
    executor: { it: "/it/architecture/executor.html", en: "/en/architecture/executor.html" },
    lifecycle: { it: "/it/architecture/lifecycle.html", en: "/en/architecture/lifecycle.html" },
    policy: { it: "/it/architecture/policy.html", en: "/en/architecture/policy.html" },
    approval_ux: { it: "/it/architecture/approval_ux.html", en: "/en/architecture/approval_ux.html" },
    multilingual_by_definition: { it: "/it/architecture/multilingual_by_definition.html", en: "/en/architecture/multilingual_by_definition.html" },
    sandbox: { it: "/it/architecture/sandbox.html", en: "/en/architecture/sandbox.html" },
    system: { it: "/it/system/", en: "/en/system/" },
    system_models: { it: "/it/system/models.html", en: "/en/system/models.html" },
    system_services: { it: "/it/system/services.html", en: "/en/system/services.html" },
    system_lre: { it: "/it/system/lre.html", en: "/en/system/lre.html" },
    system_safety: { it: "/it/system/safety.html", en: "/en/system/safety.html" },
    system_users: { it: "/it/system/users.html", en: "/en/system/users.html" },
    system_devices: { it: "/it/system/devices.html", en: "/en/system/devices.html" },
    architecture: { it: "/it/architecture/index.html", en: "/en/architecture/index.html" },
    agent_runtime: { it: "/it/architecture/agent_runtime.html", en: "/en/architecture/agent_runtime.html" },
    executor_catalog: { it: "/it/architecture/executor_catalog.html", en: "/en/architecture/executor_catalog.html" },
    remote_executors: { it: "/it/architecture/remote_executors.html", en: "/en/architecture/remote_executors.html" },
    pairing: { it: "/it/architecture/pairing.html", en: "/en/architecture/pairing.html" },
    channel: { it: "/it/architecture/channel.html", en: "/en/architecture/channel.html" },
    http_api: { it: "/it/architecture/http_api.html", en: "/en/architecture/http_api.html" },
    mail_accounts: { it: "/it/architecture/mail_accounts.html", en: "/en/architecture/mail_accounts.html" },
    telos: { it: "/it/architecture/telos.html", en: "/en/architecture/telos.html" },
    synt: { it: "/it/architecture/synt.html", en: "/en/architecture/synt.html" },
    grammar: { it: "/it/architecture/grammar.html", en: "/en/architecture/grammar.html" },
    fast_path: { it: "/it/architecture/fast_path.html", en: "/en/architecture/fast_path.html" },
    mnest: { it: "/it/architecture/mnest.html", en: "/en/architecture/mnest.html" },
    mnestoma: { it: "/it/architecture/mnestoma.html", en: "/en/architecture/mnestome.html" },
    tutor: { it: "/it/architecture/tutor.html", en: "/en/architecture/tutor.html" },
    intelligent_executors: { it: "/it/architecture/intelligent_executors.html", en: "/en/architecture/intelligent_executors.html" },
    observability: { it: "/it/architecture/observability.html", en: "/en/architecture/observability.html" },
    praxis_engine: { it: "/it/architecture/praxis_engine.html", en: "/en/architecture/praxis_engine.html" },
    scratchpad: { it: "/it/architecture/scratchpad.html", en: "/en/architecture/scratchpad.html" },
    vaglio: { it: "/it/architecture/vaglio.html", en: "/en/architecture/vaglio.html" },
    virtualization: { it: "/it/architecture/virtualization.html", en: "/en/architecture/virtualization.html" },
    multilang: { it: "/it/architecture/multilang.html", en: "/en/architecture/multilang.html" },
    skill_importer: { it: "/it/architecture/skill_importer.html", en: "/en/architecture/skill_importer.html" },
    skills_backends: { it: "/it/architecture/skills_backends.html", en: "/en/architecture/skills_backends.html" },
    dialogue_executors: { it: "/it/Metnos_Dialogo_Executor_v1.html", en: "/en/Metnos_Dialogue_Executors_v1.html" },
    dialogue: { it: "/it/Metnos_Dialogo_v1.html", en: "/en/Metnos_Dialogue_v1.html" },
    extended_perspectives: { it: "/it/Metnos_Prospettive_Estese_v1.html", en: "/en/Metnos_Extended_Perspectives_v1.html" },
    judgement_perspectives: { it: "/it/Metnos_Prospettive_Giudizio_v1.html", en: "/en/Metnos_Perspectives_Judgement_v1.html" },
    glossary: { it: "/it/Metnos_Glossario_v1.html", en: "/en/Metnos_Glossary_v1.html" },
    code: { it: "/it/code.html", en: "/en/code.html" },
    security: { en: "/security/" },
    roadmap: { it: "/it/roadmap.html", en: "/en/roadmap.html" },
  };

  const copy = {
    it: {
      product: "Documentazione Metnos",
      menu: "Apri la navigazione",
      close: "Chiudi la navigazione",
      skip: "Vai al contenuto",
      search: "Cerca nella documentazione",
      searchPlaceholder: "Cerca una pagina…",
      results: "pagine trovate",
      noResults: "Nessuna pagina corrisponde alla ricerca.",
      language: "Lingua",
      languageName: "English",
      scope: "Guide e riferimenti pubblici di Metnos",
      groups: [
        ["Inizia qui", "L'essenziale per entrare, orientarti e formulare una richiesta.", [
          ["home", "Panoramica della documentazione"], ["webui", "Aprire e usare Metnos"], ["interface", "Mappa dell'interfaccia"], ["domains", "Cosa puoi chiedere"], ["quick_tour", "Giro rapido"],
        ]],
        ["Concetti fondamentali", "Le basi di autorità, esecuzione e sicurezza.", [
          ["executor", "Executor: azioni e limiti"], ["lifecycle", "Ciclo di vita di una richiesta"], ["policy", "Policy e autorità"], ["approval_ux", "Conferme e controlli"], ["multilingual_by_definition", "Multilingue per definizione"], ["sandbox", "Sandbox"],
        ]],
        ["Sistema", "Le componenti amministrate dalla HTTP UI e il loro uso quotidiano.", [
          ["system", "Panoramica del sistema"], ["system_models", "Modelli"], ["system_services", "Servizi"], ["system_lre", "LRE (Long Run Engine)"], ["system_safety", "Safety"], ["system_users", "Utenti"], ["system_devices", "Dispositivi"],
        ]],
        ["Architettura di esecuzione", "Come i componenti collaborano, anche tra dispositivi.", [
          ["architecture", "Mappa dell'architettura"], ["agent_runtime", "Runtime dell'agente"], ["executor_catalog", "Catalogo degli executor"], ["remote_executors", "Executor remoti"], ["pairing", "Associazione dei dispositivi"], ["channel", "Canali di conversazione"], ["http_api", "Chat web e API HTTP"], ["mail_accounts", "Account di posta"],
        ]],
        ["Ragionamento, memoria e pianificazione", "Dal significato della richiesta alle conoscenze usate per rispondere.", [
          ["telos", "Telos: fini e priorità"], ["synt", "Synt: composizione del piano"], ["grammar", "Grammatica del linguaggio"], ["fast_path", "Fast path"], ["mnest", "Mnest: memoria operativa"], ["mnestoma", "Mnestoma: memoria consolidata"], ["tutor", "Tutor"], ["intelligent_executors", "Executor intelligenti"],
        ]],
        ["Operatività e piattaforma", "Osservare, verificare e far funzionare l'istanza.", [
          ["observability", "Osservabilità"], ["praxis_engine", "Motore Praxis"], ["scratchpad", "Scratchpad"], ["vaglio", "Vaglio"], ["virtualization", "Virtualizzazione"], ["multilang", "Lingue e traduzioni"],
        ]],
        ["Skill e integrazioni", "Estensioni, provider e relative garanzie.", [
          ["skill_importer", "Importare una skill"], ["skills_backends", "Servizi di esecuzione delle skill"],
        ]],
        ["Approfondimenti", "Dialoghi, prospettive e definizioni del lessico Metnos.", [
          ["dialogue_executors", "Dialogo su executor e memoria"], ["dialogue", "Dialogo su fini e limiti"], ["extended_perspectives", "Prospettive estese"], ["judgement_perspectives", "Prospettive sul giudizio"], ["glossary", "Glossario"],
        ]],
        ["Progetto", "Codice sorgente e segnalazioni di sicurezza.", [
          ["code", "Il codice di Metnos"], ["security", "Segnalare una vulnerabilità"],
        ]],
        ["Roadmap", "Direzioni del progetto e stato verificato della loro realizzazione.", [
          ["roadmap", "Linea temporale delle roadmap"],
        ]],
      ],
    },
    en: {
      product: "Metnos documentation",
      menu: "Open navigation",
      close: "Close navigation",
      skip: "Skip to content",
      search: "Search the documentation",
      searchPlaceholder: "Find a page…",
      results: "pages found",
      noResults: "No pages match this search.",
      language: "Language",
      languageName: "Italiano",
      scope: "Public Metnos guides and references",
      groups: [
        ["Start here", "The essentials for getting in, finding your way, and asking for something.", [
          ["home", "Documentation overview"], ["webui", "Opening Metnos"], ["interface", "Interface map"], ["domains", "What you can ask"], ["quick_tour", "Quick tour"],
        ]],
        ["Core concepts", "The foundations of authority, execution, and safety.", [
          ["executor", "Executors: admitted actions"], ["lifecycle", "The lifecycle of a request"], ["policy", "Policy and authority"], ["approval_ux", "Approval and review"], ["multilingual_by_definition", "Multilingual by definition"], ["sandbox", "Sandbox"],
        ]],
        ["System", "Components administered through the HTTP UI and their everyday use.", [
          ["system", "System overview"], ["system_models", "Models"], ["system_services", "Services"], ["system_lre", "LRE (Long Run Engine)"], ["system_safety", "Safety"], ["system_users", "Users"], ["system_devices", "Devices"],
        ]],
        ["Execution architecture", "How the components cooperate, including across devices.", [
          ["architecture", "Architecture map"], ["agent_runtime", "Agent runtime"], ["executor_catalog", "Executor catalog"], ["remote_executors", "Remote executors"], ["pairing", "Device pairing"], ["channel", "Conversation channels"], ["http_api", "Web chat and HTTP API"], ["mail_accounts", "Mail accounts"],
        ]],
        ["Reasoning, memory, and learning", "From the meaning of a request to the knowledge used to answer it.", [
          ["telos", "Telos: goals and priorities"], ["synt", "Synt: composing a plan"], ["grammar", "Language grammar"], ["fast_path", "Fast path"], ["mnest", "Mnest: working memory"], ["mnestoma", "Mnestome: consolidated memory"], ["tutor", "Tutor"], ["intelligent_executors", "Intelligent executors"],
        ]],
        ["Operating the platform", "Observing, verifying, and running an instance.", [
          ["observability", "Observability"], ["praxis_engine", "Praxis engine"], ["scratchpad", "Scratchpad"], ["vaglio", "Vaglio"], ["virtualization", "Virtualisation"], ["multilang", "Languages and translations"],
        ]],
        ["Skills and integrations", "Extensions, providers, and their guarantees.", [
          ["skill_importer", "Importing a skill"], ["skills_backends", "Skill backends"],
        ]],
        ["Further reading", "Dialogues, perspectives, and definitions in the Metnos vocabulary.", [
          ["dialogue_executors", "Dialogue on executors and memory"], ["dialogue", "Dialogue on goals and limits"], ["extended_perspectives", "Extended perspectives"], ["judgement_perspectives", "Perspectives on judgement"], ["glossary", "Glossary"],
        ]],
        ["Project", "Source code and vulnerability reporting.", [
          ["code", "The Metnos codebase"], ["security", "Report a vulnerability"],
        ]],
        ["Roadmap", "Project directions and their verified implementation status.", [
          ["roadmap", "Roadmap timeline"],
        ]],
      ],
    },
  };

  const lang = document.documentElement.lang === "it" ? "it" : "en";
  const text = copy[lang];
  const canonicalPath = (value) => {
    const pathname = new URL(value, window.location.origin).pathname;
    const withoutIndex = pathname.replace(/\/index\.html$/, "/");
    return withoutIndex.length > 1 ? withoutIndex.replace(/\/$/, "") : withoutIndex;
  };
  const current = canonicalPath(window.location.pathname);
  const matchingKey = Object.keys(paths).find((key) => {
    const target = paths[key][lang];
    return target && canonicalPath(target) === current;
  });
  const alternateLanguage = lang === "it" ? "en" : "it";
  let navigationTitle = "";
  let navigationGroupTitle = "";
  for (let groupIndex = 0; groupIndex < text.groups.length; groupIndex += 1) {
    const group = text.groups[groupIndex];
    for (let entryIndex = 0; entryIndex < group[2].length; entryIndex += 1) {
      if (group[2][entryIndex][0] === matchingKey) {
        navigationTitle = group[2][entryIndex][1];
        navigationGroupTitle = group[0];
        break;
      }
    }
    if (navigationTitle) break;
  }

  const element = (name, className, value) => {
    const node = document.createElement(name);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  };
  const link = (href, label, className) => {
    const node = element("a", className, label);
    node.href = href;
    return node;
  };

  const sidebar = element("aside", "wiki-sidebar tutor-exclude");
  sidebar.setAttribute("aria-label", text.product);
  const top = element("div", "wiki-sidebar-top");
  const brand = link(paths.home[lang], "METNOS", "wiki-brand");
  const product = element("p", "wiki-product", text.product);
  top.append(brand, product);

  const locale = element("div", "wiki-locale");
  const localeLabel = element("span", "wiki-locale-label", text.language);
  const matchingPaths = matchingKey ? paths[matchingKey] : null;
  const hasAlternate = Boolean(matchingPaths && matchingPaths[alternateLanguage]);
  const alternate = (matchingPaths && matchingPaths[alternateLanguage]) || paths.home[alternateLanguage];
  const localeLink = link(alternate, text.languageName, "wiki-locale-link");
  localeLink.hreflang = alternateLanguage;
  locale.append(localeLabel, localeLink);
  if (!hasAlternate) locale.hidden = true;

  const searchLabel = element("label", "wiki-search-label", text.search);
  searchLabel.htmlFor = "wiki-navigation-search";
  const search = element("input", "wiki-search");
  search.id = "wiki-navigation-search";
  search.type = "search";
  search.placeholder = text.searchPlaceholder;
  search.autocomplete = "off";
  const searchStatus = element("p", "wiki-search-status");
  searchStatus.setAttribute("aria-live", "polite");

  const tree = element("nav", "wiki-tree");
  tree.setAttribute("aria-label", text.product);
  const groups = [];
  text.groups.forEach(([title, hint, entries], index) => {
    const group = element("details", "wiki-group");
    group.open = entries.some(([key]) => key === matchingKey) || index === 0;
    const summary = element("summary", "wiki-group-summary");
    const heading = element("span", "wiki-group-title", title);
    const description = element("span", "wiki-group-hint", hint);
    summary.append(heading, description);
    const list = element("ul", "wiki-page-list");
    const rows = [];
    entries.forEach(([key, label]) => {
      const destination = paths[key]?.[lang];
      if (!destination) return;
      const item = element("li", "wiki-page-item");
      const pageLink = link(destination, label, "wiki-page-link");
      const active = key === matchingKey;
      if (active) pageLink.setAttribute("aria-current", "page");
      pageLink.dataset.wikiSearch = `${label} ${title} ${hint}`.toLocaleLowerCase(lang);
      item.append(pageLink);
      list.append(item);
      rows.push({ item, pageLink });
    });
    group.append(summary, list);
    tree.append(group);
    groups.push({ group, rows });
  });

  const footer = element("p", "wiki-sidebar-footer", text.scope);
  sidebar.append(top, locale, searchLabel, search, searchStatus, tree, footer);

  const toggle = element("button", "wiki-toggle", text.menu);
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-controls", "wiki-navigation");
  sidebar.id = "wiki-navigation";
  const overlay = element("button", "wiki-overlay");
  overlay.type = "button";
  overlay.setAttribute("aria-label", text.close);
  const closeNavigation = () => {
    document.body.classList.remove("wiki-navigation-open");
    toggle.setAttribute("aria-expanded", "false");
  };
  const openNavigation = () => {
    document.body.classList.add("wiki-navigation-open");
    toggle.setAttribute("aria-expanded", "true");
    search.focus();
  };
  toggle.addEventListener("click", () => {
    document.body.classList.contains("wiki-navigation-open") ? closeNavigation() : openNavigation();
  });
  overlay.addEventListener("click", closeNavigation);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeNavigation();
  });

  search.addEventListener("input", () => {
    const query = search.value.trim().toLocaleLowerCase(lang);
    let count = 0;
    groups.forEach(({ group, rows }) => {
      let visible = 0;
      rows.forEach(({ item, pageLink }) => {
        const matches = !query || pageLink.dataset.wikiSearch.includes(query);
        item.hidden = !matches;
        if (matches) visible += 1;
      });
      group.hidden = visible === 0;
      if (query && visible) group.open = true;
      count += visible;
    });
    searchStatus.textContent = query
      ? (count ? `${count} ${text.results}` : text.noResults)
      : "";
  });

  const body = document.body;
  body.dataset.wikiReady = "true";
  const skip = link("#wiki-content", text.skip, "wiki-skip-link");
  const view = element("div", "wiki-view");
  view.id = "wiki-content";
  const originalChildren = Array.from(body.childNodes);
  originalChildren.forEach((node) => view.append(node));
  body.append(skip, sidebar, toggle, overlay, view);
  body.classList.add("wiki-enabled");
  if (matchingKey === "home") body.classList.add("wiki-home");

  // Every page has one editorial title.  Older documents use several source
  // conventions (a "Metnos" cover, a hero, or a first chapter): normalise the
  // visible hierarchy without changing the source corpus read by Tutor.
  const isDialogue = matchingKey === "dialogue" || matchingKey === "dialogue_executors";
  if (matchingKey !== "home" && !isDialogue) {
    const fallbackTitle = document.title
      .replace(/^Metnos\s*(?:[—–-]\s*)?/i, "")
      .replace(/\s+v\d+(?:\.\d+)?$/i, "")
      .trim();
    const pageTitle = navigationTitle || fallbackTitle || "Metnos";
    const pageGroup = navigationGroupTitle || text.product;
    const titlePage = view.querySelector(".title-page");
    const hero = titlePage ? null : view.querySelector(".hero");
    let documentHeading;

    if (titlePage) {
      titlePage.classList.add("wiki-document-header");
      documentHeading = titlePage.querySelector("h1");
      if (!documentHeading) {
        documentHeading = element("h1");
        titlePage.insertBefore(documentHeading, titlePage.firstChild);
      }
      documentHeading.textContent = pageTitle;
      titlePage.insertBefore(element("p", "wiki-document-kicker", pageGroup), documentHeading);
    } else if (hero) {
      hero.classList.add("wiki-document-header");
      documentHeading = hero.querySelector("h1");
      if (!documentHeading) {
        documentHeading = element("h1");
        hero.insertBefore(documentHeading, hero.firstChild);
      }
      documentHeading.textContent = pageTitle;
      hero.insertBefore(element("p", "wiki-document-kicker", pageGroup), documentHeading);
    } else {
      const header = element("header", "wiki-document-header");
      const eyebrow = element("p", "wiki-document-kicker", pageGroup);
      documentHeading = element("h1", "wiki-document-heading", pageTitle);
      header.append(eyebrow, documentHeading);
      const firstHeading = view.querySelector("h1");
      if (firstHeading && !/^\s*(?:\d+(?:[.\s]|[-–—])|[IVXLCDM]+(?:[.\s]|[-–—]))/i.test(firstHeading.textContent)) {
        firstHeading.remove();
      }
      view.insertBefore(header, view.firstChild);
    }

    documentHeading.classList.add("wiki-document-heading");
    Array.from(view.querySelectorAll("h1")).forEach((heading) => {
      if (heading !== documentHeading) heading.classList.add("wiki-section-heading");
    });
  }

  // Moving the source into the reading pane changes document height. Restore an
  // incoming fragment afterwards so table-of-contents and shared deep links
  // land on their intended heading, rather than at the old page offset.
  if (window.location.hash.length > 1) {
    window.setTimeout(() => {
      let target;
      try {
        target = document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
      } catch (_) {
        target = null;
      }
      if (target) target.scrollIntoView();
    }, 0);
  }
})();
