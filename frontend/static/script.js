let currentPage = 1;

// Selected collections state — one Set per dropdown
const selectedCollections = {
  includeCollections: new Set(),
  excludeCollections: new Set(),
};

// Cached collection data from metadata API (avoid duplicate fetches on back/forward)
let collectionsCache = null;

// ─── Navigation ─────────────────────────────────────────────────────────────

function updateSteps() {
  document.querySelectorAll(".step").forEach((step, index) => {
    const stepNumber = index + 1;
    step.classList.remove("active", "completed");
    if (stepNumber === currentPage) {
      step.classList.add("active");
    } else if (stepNumber < currentPage) {
      step.classList.add("completed");
    }
  });
}

function goToPage(pageNumber) {
  if (pageNumber < 1 || pageNumber > 3) return;

  if (pageNumber > currentPage) {
    if (pageNumber > 1 && !sessionStorage.getItem("authenticationComplete")) return;
    if (pageNumber > 2 && !document.getElementById("connectionName").value.trim()) return;
  }

  document.querySelectorAll(".page").forEach((page) => page.classList.remove("active"));
  document.querySelectorAll(".nav-buttons").forEach((nav) => (nav.style.display = "none"));

  document.getElementById(`page${pageNumber}`).classList.add("active");
  document.getElementById(`page${pageNumber}-nav`).style.display = "flex";

  if (pageNumber === 3) populateTagDropdowns();

  currentPage = pageNumber;
  updateSteps();
}

async function nextPage() {
  if (currentPage === 1) {
    if (!sessionStorage.getItem("authenticationComplete")) {
      const success = await testConnection();
      if (!success) return;
    }
    goToPage(2);
    return;
  }

  if (currentPage === 2) {
    const connectionNameInput = document.getElementById("connectionName");
    if (!connectionNameInput.value.trim()) {
      connectionNameInput.style.border = "2px solid #DC2626";
      connectionNameInput.style.animation = "shake 0.5s";
      connectionNameInput.addEventListener("animationend", () => {
        connectionNameInput.style.animation = "";
      });
      connectionNameInput.addEventListener(
        "input",
        () => { connectionNameInput.style.border = "1px solid var(--border-color)"; },
        { once: true }
      );
      return;
    }
    goToPage(3);
    return;
  }
}

function previousPage() {
  if (currentPage > 1) goToPage(currentPage - 1);
}

// ─── Credentials ─────────────────────────────────────────────────────────────

function buildCredentialsPayload() {
  return {
    host: document.getElementById("host").value.trim(),
    port: parseInt(document.getElementById("port").value.trim(), 10) || 443,
    username: document.getElementById("username").value.trim(),
    password: document.getElementById("password").value.trim(),
  };
}

// ─── Test Connection ──────────────────────────────────────────────────────────

async function testConnection() {
  const testButton = document.querySelector(".test-connection");
  const errorElement = document.getElementById("connectionError");
  const nextButton = document.getElementById("nextButton");

  try {
    testButton.disabled = true;
    testButton.textContent = "Testing...";
    errorElement.classList.remove("visible");

    const response = await fetch("/workflows/v1/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildCredentialsPayload()),
    });

    const data = await response.json();

    if (!response.ok) throw new Error(data.message || "Connection failed");

    if (data.success) {
      testButton.innerHTML = `Connection Successful
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"
             width="20" height="20" style="margin-left: 8px">
          <path fill-rule="evenodd" d="M8.603 3.799A4.49 4.49 0 0112 2.25c1.357 0 2.573.6
            3.397 1.549a4.49 4.49 0 013.498 1.307 4.491 4.491 0 011.307 3.497A4.49 4.49 0
            0121.75 12a4.49 4.49 0 01-1.549 3.397 4.491 4.491 0 01-1.307 3.497 4.491 4.491
            0 01-3.497 1.307A4.49 4.49 0 0112 21.75a4.49 4.49 0 01-3.397-1.549 4.49 4.49 0
            01-3.498-1.306 4.491 4.491 0 01-1.307-3.498A4.49 4.49 0 012.25 12c0-1.357
            .6-2.573 1.549-3.397a4.49 4.49 0 011.307-3.497 4.49 4.49 0
            013.497-1.307zm7.007 6.387a.75.75 0 10-1.22-.872l-3.236 4.53L9.53 12.22a.75.75
            0 00-1.06 1.06l2.25 2.25a.75.75 0 001.14-.094l3.75-5.25z"
            clip-rule="evenodd" />
        </svg>`;
      testButton.style.backgroundColor = "var(--success-color)";
      testButton.style.color = "white";
      testButton.style.borderColor = "var(--success-color)";
      testButton.classList.add("success");
      nextButton.disabled = false;
      sessionStorage.setItem("authenticationComplete", "true");
      // Reset collections cache when credentials change
      collectionsCache = null;
      return true;
    } else {
      throw new Error("Connection failed");
    }
  } catch (error) {
    errorElement.textContent =
      error.message || "Failed to connect. Please check your credentials and try again.";
    errorElement.classList.add("visible");
    testButton.style.backgroundColor = "";
    testButton.style.color = "";
    testButton.style.borderColor = "";
    testButton.textContent = "Test Connection";
    testButton.classList.remove("success");
    nextButton.disabled = true;
    sessionStorage.removeItem("authenticationComplete");
    return false;
  } finally {
    testButton.disabled = false;
  }
}

// ─── Collection Dropdowns ─────────────────────────────────────────────────────

function toggleDropdown(id) {
  const dropdown = document.getElementById(id);
  const content = dropdown.querySelector(".dropdown-content");

  document.querySelectorAll(".dropdown-content").forEach((other) => {
    if (other !== content) other.classList.remove("show");
  });

  content.classList.toggle("show");
  event.stopPropagation();
}

function updateDropdownHeader(dropdownId) {
  const dropdown = document.getElementById(dropdownId);
  const header = dropdown.querySelector(".dropdown-header span");
  const selected = selectedCollections[dropdownId];

  if (selected.size === 0) {
    header.textContent = "Select collections";
  } else if (selected.size === 1) {
    header.textContent = Array.from(selected)[0];
  } else {
    header.textContent = `${selected.size} collections selected`;
  }
}

function populateCollectionList(dropdownId, collections) {
  const dropdown = document.getElementById(dropdownId);
  const content = dropdown.querySelector(".dropdown-content");
  const header = dropdown.querySelector(".dropdown-header span");

  content.innerHTML = "";

  if (!collections || collections.length === 0) {
    header.textContent = "No collections available";
    return;
  }

  header.textContent = selectedCollections[dropdownId].size > 0
    ? updateDropdownHeader(dropdownId) || header.textContent
    : "Select collections";

  collections.forEach((collection) => {
    const item = document.createElement("div");
    item.className = "tag-item";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.id = `${dropdownId}-${collection.value}`;
    checkbox.checked = selectedCollections[dropdownId].has(collection.value);

    const label = document.createElement("label");
    label.textContent = collection.title;
    label.htmlFor = checkbox.id;

    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        selectedCollections[dropdownId].add(collection.value);
      } else {
        selectedCollections[dropdownId].delete(collection.value);
      }
      updateDropdownHeader(dropdownId);
    });

    item.appendChild(checkbox);
    item.appendChild(label);
    content.appendChild(item);
  });

  updateDropdownHeader(dropdownId);
}

async function populateTagDropdowns() {
  if (collectionsCache) {
    populateCollectionList("includeCollections", collectionsCache.collections);
    populateCollectionList("excludeCollections", collectionsCache.collections);
    return;
  }

  // Show loading state
  ["includeCollections", "excludeCollections"].forEach((id) => {
    document.getElementById(id).querySelector(".dropdown-header span").textContent = "Loading...";
  });

  const credentials = buildCredentialsPayload();

  try {
    const res = await fetch("/workflows/v1/metadata", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...credentials, type: "default" }),
    });

    const data = res.ok ? await res.json() : { data: [] };
    collectionsCache = { collections: data.data || [] };
  } catch (error) {
    console.error("Error fetching collections:", error);
    collectionsCache = { collections: [] };
  }

  populateCollectionList("includeCollections", collectionsCache.collections);
  populateCollectionList("excludeCollections", collectionsCache.collections);
}

// ─── Metadata Payload ─────────────────────────────────────────────────────────

function buildMetadataPayload() {
  // Build include-collections as an object with collection keys mapped to empty arrays,
  // matching the workflow.json schema: additionalProperties: { type: "array" }
  const includeObj = Object.fromEntries(
    Array.from(selectedCollections.includeCollections).map((key) => [key, []])
  );
  const excludeObj = Object.fromEntries(
    Array.from(selectedCollections.excludeCollections).map((key) => [key, []])
  );

  return {
    "include-collections": JSON.stringify(includeObj),
    "exclude-collections": JSON.stringify(excludeObj),
  };
}

// ─── Preflight Checks ─────────────────────────────────────────────────────────

async function runPreflightChecks() {
  const checkButton = document.getElementById("runPreflightChecks");
  checkButton.disabled = true;
  checkButton.textContent = "Checking...";

  const resultsContainer = document.querySelector(".preflight-content");
  resultsContainer.innerHTML = "";

  try {
    const payload = {
      credentials: buildCredentialsPayload(),
      metadata: buildMetadataPayload(),
    };

    const response = await fetch("/workflows/v1/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) throw new Error("Network response was not ok");

    const responseJson = await response.json();

    Object.entries(responseJson.data).forEach(([, result]) => {
      const resultDiv = document.createElement("div");
      resultDiv.className = "check-result";

      const statusEl = document.createElement("div");
      statusEl.className = "check-status";

      if (result.success) {
        statusEl.innerHTML = `
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
            <path fill-rule="evenodd" d="M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365
              9.75 9.75-4.365 9.75-9.75 9.75S2.25 17.385 2.25 12zm13.36-1.814a.75.75 0
              10-1.22-.872l-3.236 4.53L9.53 12.22a.75.75 0 00-1.06 1.06l2.25 2.25a.75.75
              0 001.14-.094l3.75-5.25z" clip-rule="evenodd" />
          </svg>
          <span>${result.successMessage}</span>`;
        statusEl.classList.add("success");
      } else {
        statusEl.innerHTML = `
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
            <path fill-rule="evenodd" d="M12 2.25c-5.385 0-9.75 4.365-9.75 9.75s4.365 9.75
              9.75 9.75 9.75-4.365 9.75-9.75S17.385 2.25 12 2.25zm-1.72 6.97a.75.75 0
              10-1.06 1.06L10.94 12l-1.72 1.72a.75.75 0 101.06 1.06L12 13.06l1.72
              1.72a.75.75 0 101.06-1.06L13.06 12l1.72-1.72a.75.75 0 10-1.06-1.06L12
              10.94l-1.72-1.72z" clip-rule="evenodd" />
          </svg>
          <span>${result.failureMessage || "Check failed"}</span>`;
        statusEl.classList.add("error");
      }

      resultDiv.appendChild(statusEl);
      resultsContainer.appendChild(resultDiv);
    });
  } catch (error) {
    console.error("Preflight check failed:", error);
    const errorDiv = document.createElement("div");
    errorDiv.className = "check-status error";
    errorDiv.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
        <path fill-rule="evenodd" d="M12 2.25c-5.385 0-9.75 4.365-9.75 9.75s4.365 9.75
          9.75 9.75 9.75-4.365 9.75-9.75S17.385 2.25 12 2.25zm-1.72 6.97a.75.75 0
          10-1.06 1.06L10.94 12l-1.72 1.72a.75.75 0 101.06 1.06L12 13.06l1.72 1.72a.75.75
          0 101.06-1.06L13.06 12l1.72-1.72a.75.75 0 10-1.06-1.06L12 10.94l-1.72-1.72z"
          clip-rule="evenodd" />
      </svg>
      <span>Failed to perform check</span>`;
    resultsContainer.appendChild(errorDiv);
  } finally {
    checkButton.disabled = false;
    checkButton.textContent = "Check";
  }
}

// ─── Run Workflow ─────────────────────────────────────────────────────────────

async function handleRunWorkflow() {
  const runButton = document.getElementById("runWorkflowButton");
  const modal = document.getElementById("successModal");
  if (!runButton) return;

  try {
    runButton.disabled = true;
    runButton.textContent = "Starting...";

    const credentials = buildCredentialsPayload();
    const connectionName = document.getElementById("connectionName").value;
    const tenantId = window.env.TENANT_ID || "default";
    const appName = window.env.APP_NAME || "metabase";
    const currentEpoch = Math.floor(Date.now() / 1000);

    const payload = {
      credentials,
      connection: {
        connection_name: connectionName,
        connection_qualified_name: `${tenantId}/${appName}/${currentEpoch}`,
      },
      metadata: buildMetadataPayload(),
    };

    const response = await fetch("/workflows/v1/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) throw new Error("Failed to start workflow");

    runButton.textContent = "Started Successfully";
    runButton.classList.add("success");
    modal.classList.add("show");
  } catch (error) {
    console.error("Failed to start workflow:", error);
    runButton.textContent = "Failed to Start";
    runButton.classList.add("error");
  } finally {
    setTimeout(() => {
      runButton.disabled = false;
      runButton.textContent = "Run";
      runButton.classList.remove("success", "error");
      modal.classList.remove("show");
    }, 3000);
  }
}

// ─── DOMContentLoaded ─────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  sessionStorage.removeItem("authenticationComplete");

  // Invalidate auth on any credential field change
  ["host", "port", "username", "password"].forEach((fieldId) => {
    const field = document.getElementById(fieldId);
    if (field) {
      field.addEventListener("input", () => {
        sessionStorage.removeItem("authenticationComplete");
        collectionsCache = null;
        const testButton = document.querySelector(".test-connection");
        if (testButton) {
          testButton.style.backgroundColor = "";
          testButton.style.color = "";
          testButton.style.borderColor = "";
          testButton.textContent = "Test Connection";
          testButton.classList.remove("success");
        }
      });
    }
  });

  // Button-group toggle: click makes clicked button primary, others secondary
  document.querySelectorAll(".button-group .btn").forEach((button) => {
    button.addEventListener("click", (e) => {
      const group = e.target.closest(".button-group");
      group.querySelectorAll(".btn").forEach((btn) => {
        btn.classList.remove("btn-primary");
        btn.classList.add("btn-secondary");
      });
      e.target.classList.remove("btn-secondary");
      e.target.classList.add("btn-primary");
    });
  });

  // Preflight checks
  const checkButton = document.getElementById("runPreflightChecks");
  if (checkButton) checkButton.addEventListener("click", runPreflightChecks);

  // Run workflow
  const runButton = document.getElementById("runWorkflowButton");
  if (runButton) runButton.addEventListener("click", handleRunWorkflow);

  // Close collection dropdowns on outside click
  document.addEventListener("click", (event) => {
    document.querySelectorAll(".tag-dropdown").forEach((dropdown) => {
      if (!dropdown.contains(event.target)) {
        dropdown.querySelector(".dropdown-content").classList.remove("show");
      }
    });
  });

  // Sidebar step navigation — only allow navigating to already-visited steps
  document.querySelectorAll(".step").forEach((step) => {
    step.addEventListener("click", () => {
      const targetPage = parseInt(step.dataset.step);
      if (targetPage <= currentPage) goToPage(targetPage);
    });
  });

  // Show page1-nav on initial load
  document.getElementById("page1-nav").style.display = "flex";
});
