# Feature Specification: Prompt Audit Call Resolution

**Feature Branch**: `jmorrison-jnpr-fix-78-prompt-audit-call-resolution`

**Created**: 2026-10-28

**Status**: Draft

**Input**: Issue #78 requests accurate, ordered prompt reporting when a handler calls methods on constructed objects, uses ordinary dotted class and method calls, calls methods through `self` or `cls`, calls helpers more than once, or follows recursive call paths. It names menu rows 294, 295, 296, 297, 300, 303, and 304 as affected examples. The work is limited to the shared prompt audit tool and regression tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Find prompts through constructed objects (Priority: P1)

An engineer runs the prompt audit for an operation whose handler calls a method on an object created in the inspected source. The report includes recognized prompts reached through that method.

**Why this priority**: The audit currently misses calls when it cannot connect a constructed object's receiver to its method. This leaves affected operations without required prompt parameters.

**Independent Test**: Give the audit a small source fixture with a handler, a constructed object, and a method that calls a recognized prompt helper. Check that the report contains that prompt.

**Acceptance Scenarios**:

1. **Given** a handler calls a method directly on a constructed object, **When** the class name uniquely identifies one indexed class, **Then** the report includes prompts reached in that method.
2. **Given** a local variable receives a constructed object or a simple local alias, **When** the handler calls a method through that variable, **Then** the audit resolves the method only while its class remains identifiable.
3. **Given** a handler calls a method through a dotted class expression, **When** the trailing class name uniquely identifies one indexed class, **Then** the audit resolves that method.
4. **Given** a receiver has no statically identifiable class or its class name is ambiguous, **When** the audit encounters its method call, **Then** the report does not guess a target method.

### User Story 2 - Resolve methods in their class context (Priority: P1)

An engineer audits a method that calls another method through `self` or `cls`. The audit selects the method from the current class, even when another class defines a method with the same name.

**Why this priority**: A name-only lookup can miss a valid call or select the wrong class when method names repeat.

**Independent Test**: Give the audit two classes with same-named methods and distinct prompt helpers. Check that each method reports only the helper reached in its own class context.

**Acceptance Scenarios**:

1. **Given** a class method calls `self.helper()` and two classes define `helper`, **When** the audit analyzes the first class, **Then** it follows that class's `helper`.
2. **Given** a class method calls `cls.helper()` and two classes define `helper`, **When** the audit analyzes the first class, **Then** it follows that class's `helper`.
3. **Given** no unique or class-context target can be established, **When** the audit sees a same-named call, **Then** it leaves the target unresolved rather than selecting an arbitrary definition.
4. **Given** a handler calls `Class.method()` or `mh.Class.method()` and the class name is unique, **When** the audit resolves the call, **Then** it follows the indexed method.

### User Story 3 - Preserve prompt call order and occurrences (Priority: P1)

An engineer uses the report to determine the prompt parameters and their order for an operation. The report lists each recognized prompt at each statically reached call site.

**Why this priority**: The portal supplies answers in sequence. A missing or reordered prompt can make the operation receive the wrong answer.

**Independent Test**: Give the audit a handler with calls to different prompt helpers, including a repeated call and a repeated helper method. Compare the complete result list to the expected sequence.

**Acceptance Scenarios**:

1. **Given** a handler reaches a site prompt and then a device prompt, **When** the audit reports the handler, **Then** it lists `site` before `device`.
2. **Given** a handler reaches the same prompt helper twice, **When** the audit reports the handler, **Then** it includes that prompt kind twice.
3. **Given** two separate call sites reach the same helper method, **When** the audit reports the handler, **Then** it includes the prompts from both call sites in source call order.

### User Story 4 - Bound recursive call analysis (Priority: P2)

An engineer audits code with direct or mutual recursion. The audit returns a finite report, includes prompts found within the supported call-depth bound, and does not suppress later call sites merely because the same function was visited earlier.

**Why this priority**: Recursive paths must not hang the audit or hide valid repeated prompt calls.

**Independent Test**: Give the audit direct-recursion and mutual-recursion fixtures with prompt helpers at and beyond the depth limit. Check termination and the exact prompt sequence.

**Acceptance Scenarios**:

1. **Given** a handler reaches a recursive cycle, **When** the audit analyzes it, **Then** the analysis terminates and includes prompts reached within four call edges from the handler.
2. **Given** a prompt helper is reachable only after more than four call edges, **When** the audit analyzes the handler, **Then** that deeper prompt is not reported.
3. **Given** the same helper is reached from separate call sites before the depth limit, **When** the audit analyzes the handler, **Then** it reports each occurrence in order.

### Edge Cases

- A locally assigned receiver is overwritten with a value whose class is unknown. The audit does not keep using the earlier class association.
- A direct or dotted constructor call uses a class name that maps to multiple definitions. The audit leaves the method call unresolved.
- Two classes define the same method name, and a call has no usable `self`, `cls`, or receiver-class context. The audit does not choose either method.
- A recursive cycle contains a prompt helper before the depth limit. The report includes each statically reached occurrence within the limit and terminates.
- A recursive cycle contains no recognized prompt helper. The audit still terminates and reports no prompt for that cycle.
- A function contains prompt calls in multiple branches. The report reflects statically reached call sites in source order and does not claim to know which branch runs.
- A call uses an unresolved factory result, dynamic dispatch, or other receiver whose class cannot be established from the inspected source. The audit leaves that call unresolved.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The prompt audit MUST report recognized prompt kinds reached through a method call on an object whose class can be established from the inspected source.
- **FR-002**: The prompt audit MUST resolve direct constructed-object calls, local variables assigned from a known construction, and simple local aliases when the class remains statically identifiable.
- **FR-003**: The prompt audit MUST use the containing class context to resolve `self` and `cls` method calls when multiple classes define the same method name.
- **FR-004**: The prompt audit MUST preserve `Class.method()` and dotted class-expression calls when the trailing class name uniquely identifies one indexed class. It MUST leave calls unresolved when the class or target is ambiguous or cannot be established without guessing.
- **FR-005**: The prompt audit MUST return recognized prompt kinds in source call order, descending into each resolved call at its call site before continuing to later call sites.
- **FR-006**: The prompt audit MUST include each recognized prompt call occurrence, including repeated calls to the same helper or through separate call sites.
- **FR-007**: The prompt audit MUST follow calls no more than four call edges beyond the requested handler. It MUST include prompts in functions reached at the four-edge limit and MUST NOT inspect functions reached beyond that limit.
- **FR-008**: The prompt audit MUST terminate on direct and mutual recursive call paths without globally suppressing later, distinct call-site occurrences.
- **FR-009**: The prompt audit MUST remain a static, conservative analysis. It MUST NOT execute inspected code or claim to know runtime branch outcomes.
- **FR-010**: Regression tests MUST cover inline and assigned constructions, dotted class expressions, class-context resolution for `self` and `cls`, duplicate method names and class names, ordered and repeated prompts, unresolved receivers, and direct and mutual recursion at the depth boundary.
- **FR-011**: Regression tests MUST use self-contained source fixtures for the call-resolution patterns in this issue. Tests MUST NOT require access to the consumer repository.

### Key Entities

- **Handler**: The operation entry point whose prompt calls the audit reports.
- **Callable**: A function or method that the audit can follow from a handler or another callable.
- **Receiver**: The object or class through which a method call occurs, with a class association only when source evidence supports it.
- **Prompt occurrence**: One statically reached call to a recognized prompt helper, including its prompt kind and position in the report sequence.
- **Call depth**: The number of resolved callable edges followed beyond the requested handler, capped at four.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of regression scenarios return the expected complete prompt-kind sequence, including order and repeated occurrences.
- **SC-002**: 100% of tested `self` and `cls` calls with class context resolve to the matching class, including fixtures with duplicate method names.
- **SC-003**: 100% of tested direct and mutual recursion cases terminate and report prompts at call depths zero through four, while reporting none reachable only beyond depth four.
- **SC-004**: No regression scenario reports a prompt from a target whose receiver class or callable cannot be established from the inspected source.
- **SC-005**: Reviewers can trace 100% of reported prompt entries in the regression fixtures to a call site, and the reported order matches the fixture source order.

## Assumptions

- The audit reports statically reachable call sites in source order. It does not evaluate conditions or predict which runtime branch executes.
- A direct construction, a dotted construction with a unique trailing class name, a local variable assigned from that construction, and a simple same-scope name-to-name alias provide sufficient evidence for receiver-class resolution. Factory results, ambiguous class names, dynamic dispatch, and unsupported assignment flows remain unresolved.
- Four call edges beyond the requested handler remain the maximum analysis depth. The starting handler is at depth zero.
- This specification covers only the shared prompt audit tool and its regression tests. It does not change MistHelper, consumer registries, branches, or other source files.
- The seven menu row numbers in the issue are context for the reported symptom. Regression tests use local fixtures and do not require a consumer checkout.
- The dedicated worktree has already selected the branch named in the feature metadata. This workflow does not create, rename, or switch branches.
