2. WHERE accepts ONLY single members [Dim].[Dim].[Element]
   - never .Members, never {set expressions}, never .Children, never Descendants(...)
3. A dimension must appear on exactly ONE of: COLUMNS, ROWS, or WHERE - never in two places, never omitted
4. ALL dimensions not on COLUMNS or ROWS MUST appear in WHERE - include every remaining dimension with one element
5. A 4-digit year (e.g. 2025) ALWAYS goes in the dimension whose name contains "Year" or "Period" - NEVER Employee, Scenario, etc.
6. For unfiltered WHERE dimensions, choose a default from the model profile when available; otherwise use that dimension's "default_element". Do not choose an arbitrary leaf when default_element exists.
7. Put dimensions needed for the answer on COLUMNS or ROWS; put all remaining dimensions in WHERE.
8. PREFER set functions over enumeration when the axis would list more than 5 sibling elements that share a parent:
   - All immediate children of a parent: [Dim].[Dim].[Parent].Children
   - Parent plus immediate children: DRILLDOWNLEVEL({[Dim].[Dim].[Parent]})
   - Full hierarchy under a parent: Descendants([Dim].[Dim].[Parent])
   - Leaf-only descendants under a parent: Descendants([Dim].[Dim].[Parent], , LEAVES)
   - Descendants at one level: Descendants([Dim].[Dim].[Parent], 1)
   The Parent MUST come from the dimension's "consolidations" or "top_consolidations" list.
9. NEVER call .Children, Descendants, or .Members on a leaf element (an element not in "consolidations").
10. For full financial statements (P&L, income statement, balance sheet, cash flow) on the Account/Line Item dimension, use
    Descendants([Dim].[Dim].[TopConsolidation]) with a name from "top_consolidations" or "consolidations" -
    do NOT enumerate every line item by hand.
11. For a non-statement request where the user directly names a consolidated element, keep the result bounded:
    use DRILLDOWNLEVEL({[Dim].[Dim].[NamedParent]}) to show the named parent plus immediate children.
    If TM1 rejects DRILLDOWNLEVEL, use Union({[Dim].[Dim].[NamedParent]}, [Dim].[Dim].[NamedParent].Children).
    Do NOT use Descendants([Dim].[Dim].[NamedParent]) unless the user explicitly asks for all
    descendants / full hierarchy.
12. For "by month" on a Period/Time dimension that has a yearly parent: prefer [Period].[Period].[<year>].Children
    over enumerating 12 month names.
13. For relative period questions on ordered time dimensions:
    - one adjacent period: use .NextMember / .PrevMember or .Lead(1) / .Lag(1)
    - N periods ending at a named period: use LastPeriods(N, [Dim].[Dim].[Period])
    - N periods starting at a named period: use LastPeriods(-N, [Dim].[Dim].[Period]) and sort if chronological order matters
    Do not enumerate long moving windows by hand.
14. If using an existing TM1 subset as a building block, prefer TM1SubsetToSet([Dim], "Subset Name")
    over [Dim].[Subset Name] because subset names can collide with member or level names.
    If you need the first member of a subset as a cube coordinate, wrap it as
    TM1Member(TM1SubsetToSet([Dim], "Subset Name").Item(0), 0).
15. Avoid [Dim].[Dim].Members unless the user explicitly asks for every element in that dimension.
16. For dimensions with mixed hierarchy levels, prefer either a set function rooted at a clean parent or an explicit small list -
    do NOT mix rollups and leaves on the same axis.
17. When the question names one specific non-time leaf element, use that element in WHERE unless the user asks to compare it against siblings.
18. For financial statement requests (P&L, profit and loss statement, income statement), show statement line items by putting the Account/Line Item/P&L account dimension on ROWS. Do not hide the account dimension in WHERE as a single total unless the user asked for a single total.
19. Single ROWS dimension: {<set-function-or-enumeration>} ON ROWS
20. Multiple ROWS dimensions: {setA} * {setB} ON ROWS
    - use the * operator for cross product; NEVER use CrossJoin() function (causes rte 45 in TM1)
21. Use ONLY element names from the lists above; match case-insensitively to the exact entry
22. CRITICAL: verify which dimension each element belongs to before writing it - wrong dimension = hard error
23. TM1 element display names and other attributes (e.g. "Employee Name", "Grade") are stored as
    dimension attributes - do NOT query a different cube to look up names or labels
24. "Employee no.2", "employee #2", or "employee 2" means the Employee element named exactly "2";
    do not substitute a nearby visible ID like "10" and do not use the Full Name attribute as the MDX element
25. Time dimensions are semantic, not generic rollups: do not choose "All YTD", "All FYTD",
    "All YTG", "All FYTG", "All QTD", "All MTD", or similar cumulative elements unless
    the user explicitly asks for YTD/FYTD/YTG/QTD/MTD/full-year/all-periods, or the model profile default explicitly names that element.
26. If the user gives a specific month, use that exact month element. If no month is
    specified and the model profile provides a Month default, use that default.
    For year-only comparisons such as 2024 vs 2023, prior year, previous year,
    or YoY with no specific month/quarter, use the all-month/all-period
    consolidation for the Month/Period dimension instead of the current/default month.
27. Concrete member grounding: when the user names a specific member/entity/category,
    use ONLY accepted matching members from "Grounded member candidates" below.
    For entity/company names, only candidates marked accepted_for_entity_mentions=true
    may satisfy that mention; same-name Segment/Intercompany/classifier members are
    disambiguation context only. If a concrete member is not present there, use a
    default_element or ask for repair through validation; do not invent names.
