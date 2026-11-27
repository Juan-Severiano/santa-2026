// SPDX-License-Identifier: Apache-2.0
// Local multi-output plan ranking. Included inside lns.rs's private engine.
// Arithmetic/catalog/replay infrastructure: Tony Li and konbu17 via repair.rs.
// See THIRD_PARTY.md and licenses/Apache-2.0.txt.

const FRONTIER_PLANS_PER_FAMILY: usize = 12;
struct PlanChoice { plan: Option<Vec<Step>>, plans: usize, catalogs: usize }

fn frontier_plan_key(plan: &[Step]) -> Vec<(Int,u8,Int,Int)> {
    let mut key: Vec<_> = plan.iter().map(|s| {
        let (a,b) = if (s.op==b'+' || s.op==b'*') && s.a>s.b { (s.b,s.a) } else { (s.a,s.b) };
        (s.value,s.op,a,b)
    }).collect(); key.sort_unstable(); key
}

// Exact distances through two operations in the context extended by this plan.
// The value 3 means "not reachable in <=2", not an exact distance or bound.
fn frontier_distance(search: &Searcher, added: &HashSet<Int>, target: Int) -> usize {
    let known = |v: Int| search.known.contains(&v) || added.contains(&v);
    if known(target) { return 0; }
    let one = |v: Int| search.one.contains_key(&v) || added.iter().any(|&h|
        inverses(v,h,search.limit).iter().any(|inv|known(inv.value)));
    if one(target) { return 1; }
    for &atom in search.pool.iter().chain(added.iter()) {
        if inverses(target,atom,search.limit).iter().any(|inv|known(inv.value)||one(inv.value)) { return 2; }
    }
    if target%2==0 && one(target/2) { return 2; }
    let root=square_root(target);
    if checked(b'*',root,root,search.limit)==Some(target) && one(root) { return 2; }
    3
}

fn frontier_plan_rank(search: &Searcher, plan: &[Step], pending: &[Int])
    -> (usize,usize,Vec<(Int,u8,Int,Int)>) {
    let added: HashSet<Int> = plan.iter().map(|s|s.value).collect();
    let distinct: BTreeSet<Int> = pending.iter().copied().collect();
    let residual: usize = distinct.iter().map(|&t|frontier_distance(search,&added,t)).sum();
    (plan.len()+residual,plan.len(),frontier_plan_key(plan))
}

// Subqueries use normalize rather than finish: an old partial derivation must
// remain usable inside a different final root plan. The same explicit cap also
// bounds each two-operation subquery of the chain family.
fn frontier_two_plans(search: &Searcher, target: Int, deadline: Instant) -> Vec<Vec<Step>> {
    let mut plans=Vec::new(); let mut seen=HashSet::new();
    let mut offer=|steps: Vec<Step>| {
        if let Some(plan)=search.normalize(steps,target,2) {
            if seen.insert(frontier_plan_key(&plan)) { plans.push(plan); }
        }
        plans.len()
    };
    let mut count=0;
    if let Some(plan)=search.c01(target) { count=offer(plan); }
    for &atom in &search.pool {
        if Instant::now()>=deadline || count>=FRONTIER_PLANS_PER_FAMILY { break; }
        for inv in inverses(target,atom,search.limit) {
            if let Some(mut plan)=search.c01(inv.value) { plan.push(closing(inv,atom,target)); count=offer(plan); }
        }
    }
    for (op,value) in [(b'+',if target%2==0 {target/2} else {0}),(b'*',square_root(target))] {
        if value>0 && checked(op,value,value,search.limit)==Some(target) {
            if let Some(mut plan)=search.c01(value) {
                plan.push(Step{op,a:value,b:value,value:target}); offer(plan);
            }
        }
    }
    plans.truncate(FRONTIER_PLANS_PER_FAMILY); plans
}

fn choose_frontier_plan(search: &mut Searcher, target: Int, pending: &[Int], maximum: usize,
                        deadline: Instant) -> PlanChoice {
    let mut result=PlanChoice {plan:None,plans:0,catalogs:0};
    let mut best_rank=None; let mut seen=HashSet::new(); let max=maximum.min(3);
    let mut offer=|search: &mut Searcher, steps: Vec<Step>| -> bool {
        if Instant::now()>=deadline { return false; }
        let Some(plan)=search.finish(steps,target,max) else {return false;};
        if plan.len()<search.minimum || !seen.insert(frontier_plan_key(&plan)) { return false; }
        let rank=frontier_plan_rank(search,&plan,pending); result.plans+=1;
        if best_rank.as_ref().map_or(true,|old|rank<*old) { best_rank=Some(rank); result.plan=Some(plan); }
        true
    };
    if Instant::now()>=deadline { return result; }
    // Family 1: direct and two-operation plans, including repeated operands.
    for plan in frontier_two_plans(search,target,deadline) { offer(search,plan); }
    if max<3 || Instant::now()>=deadline { return result; }
    // Family 2: S1 op S1. Keep alternatives with different paid helpers.
    let mut count=0;
    'balanced: for index in 0..search.one_values.len() {
        if Instant::now()>=deadline { break; }
        let atom=search.one_values[index];
        for inv in inverses(target,atom,search.limit) {
            if let Some(&other)=search.one.get(&inv.value) {
                let steps=vec![search.one[&atom],other,closing(inv,atom,target)];
                if offer(search,steps) { count+=1; if count>=FRONTIER_PLANS_PER_FAMILY {break 'balanced;} }
            }
        }
    }
    // Family 3: (S1 op P) op P; exact divisions may increase intermediate values.
    count=0;
    'chain: for index in 0..search.pool.len() {
        if Instant::now()>=deadline { break; }
        let atom=search.pool[index];
        for inv in inverses(target,atom,search.limit) {
            for mut plan in frontier_two_plans(search,inv.value,deadline) {
                plan.push(closing(inv,atom,target));
                if offer(search,plan) { count+=1; if count>=FRONTIER_PLANS_PER_FAMILY {break 'chain;} }
            }
        }
    }
    result
}

#[cfg(test)]
mod plan_frontier_tests {
    use super::*;
    fn search(pool: Vec<Int>,limit: Int) -> Searcher {
        Searcher::new(pool,limit,Instant::now()+Duration::from_secs(2)).unwrap()
    }
    #[test]
    fn context_overlay_matches_existing_exact_two_operation_search() {
        let base=vec![2,3,11,17,23,31]; let s=search(base.clone(),2000);
        let added=HashSet::from([22,53,583]); let mut extended=base;
        extended.extend(added.iter().copied()); let mut exact=search(extended,2000);
        for target in [1,2,5,22,53,55,58,121,161,583,986,1999] {
            assert_eq!(frontier_distance(&s,&added,target),exact.cost2(target).map_or(3,|p|p.len()));
        }
    }
    #[test]
    fn final_identity_is_skipped_but_old_partial_helpers_remain_legal() {
        let mut s=search(vec![2,3,7],1000); s.root=11;
        s.identity=vec![(9,b'*',3,3),(11,b'+',2,9)];
        let c=choose_frontier_plan(&mut s,11,&[],2,Instant::now()+Duration::from_secs(1));
        let plan=c.plan.unwrap(); assert!(plan.iter().any(|s|s.value!=9 && s.value!=11));
        assert!(s.skipped_identity>0); assert!(frontier_two_plans(&s,9,Instant::now()+Duration::from_secs(1)).len()>0);
    }
    #[test]
    fn alternatives_complete_the_three_output_toy_in_eight_paid_operations() {
        let mut p=Puzzle {limit:2000,starts:vec![2,3,11,17,23,31],targets:vec![161,583,986],
            definitions:BTreeMap::new(),arguments:BTreeMap::new(),order:Vec::new()};
        let hint=vec![[b'*' as i32,2,2,6],[b'+' as i32,4,3,7],[b'+' as i32,6,7,8],
            [b'*' as i32,4,4,9],[b'+' as i32,5,4,10],[b'+' as i32,9,10,11],
            [b'*' as i32,3,0,12],[b'-' as i32,5,0,13],[b'*' as i32,12,13,14]];
        let (cold,_)=optimize(p.clone(),&hint,5,100,16).unwrap(); assert_eq!(cold.len(),9);
        p.load_hint(&hint).unwrap(); let (removed,mut available)=frontier_context(&p,&p.targets).unwrap();
        let mut pending=vec![583,161,986]; let mut steps=Vec::new();
        while !pending.is_empty() {
            let target=pending.remove(0); let mut s=search(available.clone(),p.limit);
            let choice=choose_frontier_plan(&mut s,target,&pending,3,Instant::now()+Duration::from_secs(1));
            assert!(choice.plans<=3*FRONTIER_PLANS_PER_FAMILY); assert_eq!(choice.catalogs,0);
            for step in choice.plan.unwrap() {
                if !available.contains(&step.value) {available.push(step.value);steps.push(step);}
            }
            pending.retain(|v|!available.contains(v));
        }
        let trial=rebuild(&p,&removed,986,&steps).unwrap();
        assert_eq!(verified_program(&trial).unwrap().len(),8);
    }
    #[test]
    fn expired_deadline_and_instruction_budget_are_respected() {
        let mut s=search(vec![2,3,7],1000);
        assert!(choose_frontier_plan(&mut s,11,&[],3,Instant::now()).plan.is_none());
        assert!(choose_frontier_plan(&mut s,11,&[],1,Instant::now()+Duration::from_secs(1)).plan.is_none());
    }
}
