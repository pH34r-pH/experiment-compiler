(() => {
  const catalog = document.getElementById("catalog");
  const table = document.getElementById("catalog-table");
  const controls = document.getElementById("catalog-controls");
  const filter = document.getElementById("catalog-filter");
  const status = document.getElementById("catalog-status");
  if (!catalog || !table || !controls || !filter || !status) return;

  const groups = [...table.querySelectorAll("tbody.catalog-group")];
  const sortLabels = {
    id: "Experiment record",
    "package-bytes": "Package size",
    attempts: "Attempts",
    "eval-mse": "Eval MSE",
  };
  const sortAttributes = {
    "package-bytes": "sortPackageBytes",
    attempts: "sortAttempts",
    "eval-mse": "sortEvalMse",
  };
  let sortState = {key: "id", direction: "ascending"};

  for (const heading of table.querySelectorAll("thead th[data-sort-key]")) {
    const key = heading.dataset.sortKey;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "catalog-sort";
    button.dataset.sort = key;
    button.textContent = heading.textContent;
    button.setAttribute("aria-label", `Sort within each family by ${sortLabels[key]}`);
    heading.replaceChildren(button);
    button.addEventListener("click", () => {
      sortState = {
        key,
        direction: sortState.key === key && sortState.direction === "ascending"
          ? "descending" : "ascending",
      };
      sortRows();
    });
  }

  function sortValue(row, key) {
    if (key === "id") return row.dataset.sortId || "";
    const value = row.dataset[sortAttributes[key]];
    if (value === "" || value === undefined) return null;
    if (key === "eval-mse" && value.includes("/")) {
      const [rawNumerator, rawDenominator] = value.split("/");
      let numerator = BigInt(rawNumerator);
      let denominator = BigInt(rawDenominator);
      if (denominator < 0n) {
        numerator *= -1n;
        denominator *= -1n;
      }
      return {numerator, denominator};
    }
    return Number(value);
  }

  function sortRows() {
    for (const heading of table.querySelectorAll("thead th[data-sort-key]")) {
      const button = heading.querySelector("button");
      if (heading.dataset.sortKey === sortState.key) {
        heading.setAttribute("aria-sort", sortState.direction);
        button.setAttribute("aria-pressed", "true");
        button.setAttribute("aria-label", `Sort within each family by ${sortLabels[sortState.key]}, ${sortState.direction}`);
      } else {
        heading.removeAttribute("aria-sort");
        button.setAttribute("aria-pressed", "false");
        button.setAttribute("aria-label", `Sort within each family by ${sortLabels[heading.dataset.sortKey]}`);
      }
    }

    for (const group of groups) {
      const records = [...group.querySelectorAll("tr.catalog-record")];
      records.sort((left, right) => {
        const a = sortValue(left, sortState.key);
        const b = sortValue(right, sortState.key);
        if (a === null || b === null) {
          if (a === null && b !== null) return 1;
          if (a !== null && b === null) return -1;
          return left.dataset.recordId.localeCompare(right.dataset.recordId);
        }
        let comparison;
        if (typeof a === "object" && typeof b === "object") {
          const difference = a.numerator * b.denominator - b.numerator * a.denominator;
          comparison = difference < 0n ? -1 : difference > 0n ? 1 : 0;
        } else {
          const numeric = value => typeof value === "object"
            ? Number(value.numerator) / Number(value.denominator) : value;
          comparison = typeof a === "number" || typeof b === "number"
            ? numeric(a) - numeric(b) : String(a).localeCompare(String(b));
        }
        if (comparison === 0) return left.dataset.recordId.localeCompare(right.dataset.recordId);
        if (sortState.direction === "descending") comparison *= -1;
        return comparison;
      });
      for (const row of records) group.append(row);
    }
  }

  function applyFilter() {
    const query = filter.value.normalize("NFKC").toLocaleLowerCase().trim();
    let visible = 0;
    let total = 0;
    for (const group of groups) {
      const rows = [...group.querySelectorAll("tr.catalog-record")];
      let groupVisible = 0;
      for (const row of rows) {
        total += 1;
        const match = !query || row.dataset.search.includes(query);
        row.hidden = !match;
        if (match) groupVisible += 1;
      }
      visible += groupVisible;
      group.hidden = groupVisible === 0;
    }
    const empty = table.querySelector("tfoot");
    if (empty) empty.hidden = visible !== 0;
    status.textContent = query
      ? `Showing ${visible} of ${total} records matching “${filter.value.trim()}”.`
      : `Showing all ${total} records.`;
  }

  filter.addEventListener("input", applyFilter);
  controls.hidden = false;
  sortRows();
  applyFilter();
})();
