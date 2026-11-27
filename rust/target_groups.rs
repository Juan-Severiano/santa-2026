// SPDX-License-Identifier: Apache-2.0
// Locally developed deterministic grouping of actual targets by dependency support.
// Uses the attributed graph/replay primitives from repair.rs and lns.rs;
// see THIRD_PARTY.md for the Tony Li / konbu17 repair ancestry.
// Include in the existing LNS engine module; this file does not change legacy search.

fn related_target_blocks(puzzle: &Puzzle, maximum: usize) -> Outcome<Vec<Vec<Int>>> {
    if maximum == 0 { return Ok(Vec::new()); }
    // Validate the original graph before collecting supports. No existing value
    // becomes a free leaf here; the caller still obtains its exact legal pool.
    active_values(puzzle)?;
    let seeds: BTreeSet<Int> = puzzle.starts.iter().copied().collect();
    let targets: BTreeSet<Int> = puzzle.targets.iter().copied()
        .filter(|value| puzzle.arguments.contains_key(value) && !seeds.contains(value))
        .collect();
    if targets.len() < 3 { return Ok(Vec::new()); }

    let mut supports: BTreeMap<Int, BTreeSet<Int>> = BTreeMap::new();
    for &target in &targets {
        let mut pending = vec![target];
        let mut visited = BTreeSet::new();
        while let Some(value) = pending.pop() {
            if seeds.contains(&value) || !visited.insert(value) { continue; }
            if !targets.contains(&value) && puzzle.arguments.contains_key(&value) {
                supports.entry(value).or_default().insert(target);
            }
            if let Some(&(left, right)) = puzzle.arguments.get(&value) {
                pending.push(left); pending.push(right);
            }
        }
    }

    let mut groups = BTreeSet::new();
    for support in supports.values() {
        // Keep the complete support, not a subset that leaves another target
        // using the scaffold. Larger supports remain for other neighborhoods.
        if (3..=8).contains(&support.len()) {
            groups.insert(support.iter().copied().collect::<Vec<_>>());
        }
    }

    let mut ranked = Vec::new();
    for group in groups {
        let (removed, _) = frontier_context(puzzle, &group)?;
        if removed.len() <= group.len() { continue; }
        let shared_removed = removed.iter().filter(|value| {
            !targets.contains(value) && !seeds.contains(value) &&
                supports.get(value).is_some_and(|support|
                    group.iter().filter(|target| support.contains(target)).take(2).count() == 2)
        }).count();
        if shared_removed == 0 { continue; }
        let fee = removed.len() - group.len();
        ranked.push((shared_removed, fee, group));
    }
    ranked.sort_by(|left, right| right.0.cmp(&left.0)
        .then_with(|| right.1.cmp(&left.1))
        .then_with(|| left.2.cmp(&right.2)));
    Ok(ranked.into_iter().take(maximum).map(|(_, _, group)| group).collect())
}

#[cfg(test)]
mod target_groups_tests {
    use super::*;

    fn fixture(starts: Vec<Int>, targets: Vec<Int>, hint: Program) -> Puzzle {
        let mut puzzle = Puzzle { limit: 1_000_000, starts, targets,
            definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() };
        puzzle.load_hint(&hint).unwrap();
        puzzle
    }

    fn shared_fixture(four: bool) -> Puzzle {
        // Shared 6 feeds noncontiguous actual targets; 35 and 49 are unrelated.
        let mut hint = vec![[b'*' as i32,0,1,4], [b'+' as i32,4,3,5],
            [b'*' as i32,2,3,6], [b'*' as i32,4,2,7],
            [b'*' as i32,3,3,8], [b'+' as i32,4,2,9]];
        let mut targets = vec![13,35,30,49,11];
        if four { hint.push([b'*' as i32,4,3,10]); targets.push(42); }
        fixture(vec![2,3,5,7],targets,hint)
    }

    #[test]
    fn related_three_targets_include_noncontiguous_full_support() {
        let puzzle = shared_fixture(false);
        let groups = related_target_blocks(&puzzle,32).unwrap();
        assert_eq!(groups,vec![vec![11,13,30]]);
        let (removed,pool) = frontier_context(&puzzle,&groups[0]).unwrap();
        assert!(removed.contains(&6));
        assert!(!pool.contains(&6));
        assert_eq!(removed.len(),4);
    }

    #[test]
    fn related_four_targets_never_omit_a_scaffold_consumer() {
        let puzzle = shared_fixture(true);
        assert_eq!(related_target_blocks(&puzzle,32).unwrap(),vec![vec![11,13,30,42]]);
    }

    #[test]
    fn recomputing_a_seed_does_not_create_a_free_shared_helper() {
        let puzzle = fixture(vec![2,3,5,6],vec![9,11,18],vec![
            [b'*' as i32,0,1,4], [b'+' as i32,4,1,5],
            [b'+' as i32,4,2,6], [b'*' as i32,4,1,7]]);
        assert!(related_target_blocks(&puzzle,32).unwrap().is_empty());
    }

    #[test]
    fn zero_budget_does_not_collect_groups() {
        assert!(related_target_blocks(&shared_fixture(false),0).unwrap().is_empty());
    }

    #[test]
    fn grouping_is_deterministic_and_respects_a_tied_cap() {
        let puzzle = fixture(vec![2,3,5,7],vec![13,51,30,147,11,54],vec![
            [b'*' as i32,0,1,4], [b'*' as i32,3,3,5],
            [b'+' as i32,4,3,6], [b'+' as i32,5,0,7],
            [b'*' as i32,4,2,8], [b'*' as i32,5,1,9],
            [b'+' as i32,4,2,10], [b'+' as i32,5,2,11]]);
        let groups = related_target_blocks(&puzzle,2).unwrap();
        assert_eq!(groups,vec![vec![11,13,30],vec![51,54,147]]);
        assert_eq!(related_target_blocks(&puzzle,1).unwrap(),groups[..1]);
        assert_eq!(related_target_blocks(&puzzle,2).unwrap(),groups);
    }

    #[test]
    fn several_shared_ancestors_deduplicate_to_one_target_group() {
        let puzzle = fixture(vec![2,3,5,7],vec![39,41,43],vec![
            [b'*' as i32,0,1,4], [b'*' as i32,4,4,5],
            [b'+' as i32,5,1,6], [b'+' as i32,5,2,7],
            [b'+' as i32,5,3,8]]);
        let groups = related_target_blocks(&puzzle,32).unwrap();
        assert_eq!(groups,vec![vec![39,41,43]]);
        assert_eq!(frontier_context(&puzzle,&groups[0]).unwrap().0.len(),5);
    }
}
