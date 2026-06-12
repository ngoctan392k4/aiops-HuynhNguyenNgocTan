# Findings


## 1. **Which similarity function did you choose for Layer 2, and why?** Reference at least one alternative you considered and an empirical reason for choosing the one you did.

For Layer 2, I chose a **hybrid weighted similarity function** instead of relying on only one evidence source.

The final similarity score is calculated as:

```text
score = 0.42 * log_score + 0.22 * token_score + 0.22 * trace_score + 0.10 * service_score + 0.04 * metric_score
```

I chose this function since each incident contains different types of evidence: logs, traces, affected services, and metrics. Logs are usually the strongest signal because repeated incidents often produce similar error messages. However, only logs can be misleading when a downstream service produces many errors even though it is not the real root cause.

Alternative: log-only Jaccard similarity. This is a simpler method for clear repeated patterns, but it failed for cases with conflicting evidence. For example, if logs mention payment errors but traces show the strongest failure path from `cart-svc` to `cart-redis`, a log-only model may recommend the wrong payment action. Therefore, I added trace similarity and affected-service overlap so that the engine can balance log evidence with runtime dependency evidence.

This hybrid function worked well on the evaluation set because it allowed the engine to both identify repeated incidents and detect cases where the evidence was conflicting or novel.


## 2. **How does outcome-weighted voting change the candidate ranking versus a pure-similarity ranking?** Demonstrate with a concrete eval incident.

A pure-similarity ranking only answers which historical incidents look most similar the incident. After retrieving the top similar incidents, each historical incident votes for its previous action using outcome-weighted voting.

The vote weight is based on:

```text
vote_weight = similarity
            * outcome_weight
            / sqrt(rank)
            * mttr_bonus
```

The outcome weights are:

```text
success = 1.0
partial = 0.55
failed = 0.15
```

This means that a similar historical incident with a failed outcome contributes less than a similar incident with a successful outcome. So, the ranking can change from most similar incident to most reliable action based on similar successful incidents.


## 3. **For one eval incident, explain the EV calculation in full** — the candidate set, weights, P_success values, costs, and which action won and by how much.

The engine uses an **EV-style decision proxy** based on outcome-weighted voting, confidence calculation, evidence override gates, and a blast-radius gate.

For example, consider E01.

The candidate actions come from historical neighbor votes. Each neighbor contributes votes based on:

```text
similarity score
historical outcome
neighbor rank
historical MTTR
```

The engine
 then aggregates votes by action name. The candidate set can include actions such as:

```text
increase_pool_size
rollback_service
restart_pod
page_oncall
```

The approximate confidence is calculated using:

```text
confidence = (selected_action_vote / total_vote) * 0.55 + best_similarity * 0.75
```

This means the selected action receives higher confidence when:

1. It has strong consensus among the outcome-weighted votes.
2. The current incident has a high similarity to the best historical neighbor.

The engine also checks action safety. If an action has a high blast radius, the engine escalates to `page_oncall` unless there is an explicit evidence override. This prevents the system from taking risky automated actions only because they received votes.

For E01, the evidence points strongly to a repeated payment-related failure pattern. Because the selected action has enough historical support and does not violate the blast-radius gate, the engine can auto-act instead of escalating. In this case, the action selected by the engine is based on weighted historical evidence rather than a hardcoded mapping from root cause class to action.

So, while this is not a mathematical EV calculation with explicit monetary cost, it still follows the same idea: choose the action with the best combination of historical success evidence, confidence, and operational safety.


## 4. **When did your engine choose to escalate (page_oncall) instead of auto-act?** Was that choice correct against the eval ground truth?


The engine chooses `page_oncall` when the evidence suggests that auto-remediation is unsafe, novel, or outside the action catalog.

There are several escalation gates in the code:

```text
1. Kubernetes informer/cache-staleness evidence
   -> page_oncall

2. Certificate or TLS rotation evidence
   -> page_oncall

3. Very low similarity and weak votes
   -> page_oncall

4. High blast radius action without override
   -> page_oncall
```

For example, if the trigger rule or logs contain informer/cache-stale evidence, the engine treats it as an out-of-distribution Kubernetes control-plane issue and escalates. If the logs contain certificate, x509, or TLS handshake evidence, the engine also escalates because certificate rotation is considered a human/cert-ops responsibility rather than a safe auto-remediation action.

Against the evaluation ground truth, these escalation choices were correct when the expected accepted action included `page_oncall`. This is important because the lab does not only reward auto-action. It also expects the engine to recognize when it should not act automatically.



## 5. **What is the most likely class of incident that breaks your engine?** Propose one concrete improvement that would help, but explain why you did not implement it within the time budget.


The most likely class of incident that breaks my engine is a **novel incident whose logs look similar to a known incident, but whose true root cause is different**.

The reason is that my similarity function still gives the largest weight to log evidence:

```text
log_score = 0.42
token_score = 0.22
```

Together, log-related evidence has more influence than trace, service, and metric evidence. This is useful for repeated incidents, but risky for cascade failures. In a cascade, downstream services may generate many familiar error logs even though the real root cause is upstream or in a dependency.

One concrete improvement would be to add **temporal root-cause ranking**. Instead of only comparing aggregated evidence over the whole incident window, the engine should identify which signal changed first. For example, it should ask:

```text
Which service showed abnormal metrics first?
Which trace edge started failing first?
Which log signature appeared before the others?
```

The earliest abnormal signal should receive more root-cause weight. This would help distinguish real root causes from downstream symptoms.

I did not implement this within the time budget because it requires more careful timestamp processing, windowing, clock-skew handling, and validation. The current implementation focuses on a simpler and explainable approach: hybrid similarity, outcome-weighted voting, evidence override gates, and blast-radius safety checks.
