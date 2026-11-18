// SPDX-License-Identifier: Apache-2.0
// Go port of the frozen mode-6 arithmetic repair engine.
// Original solver: Tony Li, https://www.kaggle.com/code/tonylica/elf-calculator-with-register-reuse
// Parameterized portfolio: konbu17, https://www.kaggle.com/code/konbu17/santa-2026-wider-dictionary-portfolio-163k
// Repository modifications and this port retain the same mode-6 algorithm.
// See THIRD_PARTY.md and licenses/Apache-2.0.txt.
package main

import (
	"bufio"
	"fmt"
	"io"
	"math"
	"math/bits"
	"os"
	"sort"
	"strconv"
)

var alpha = 0.18
var nDone, nRecent, indexBits = 52, 56, 20
var orderSeed uint64
var repairTrials, repairPasses = 12, 1

type Expr struct {
	V    int64
	A, B int32
	Op   byte
	Cost int32
}
type Pair struct {
	V  int64
	ID int32
}
type Operands struct{ A, B int64 }
type Instruction struct {
	Op      byte
	A, B, D int32
}
type IntSet map[int64]bool

func keys[V any](values map[int64]V) []int64 {
	result := make([]int64, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Slice(result, func(i, j int) bool { return result[i] < result[j] })
	return result
}
func abs(value int64) int64 {
	if value < 0 {
		return -value
	}
	return value
}
func apply(op byte, a, b int64) int64 {
	switch op {
	case '+':
		return a + b
	case '-':
		return a - b
	case '/':
		return a / b
	default:
		return a * b
	}
}

type Index struct {
	Keys []int64
	IDs  []int32
	Used []uint32
	Mask uint64
}

func newIndex() Index {
	size := 1 << indexBits
	return Index{Keys: make([]int64, size), IDs: make([]int32, size), Mask: uint64(size - 1)}
}
func (x *Index) slot(value int64) int {
	h := uint64(value) * uint64(0x9e3779b97f4a7c15)
	h ^= h >> 31
	i := h & x.Mask
	for x.Keys[i] != 0 && x.Keys[i] != value {
		i = (i + 1) & x.Mask
	}
	return int(i)
}
func (x *Index) get(value int64) int {
	i := x.slot(value)
	if x.Keys[i] != 0 {
		return int(x.IDs[i])
	}
	return -1
}
func (x *Index) insert(value int64, id int) bool {
	i := x.slot(value)
	if x.Keys[i] != 0 || len(x.Used)*8 > len(x.Keys)*7 {
		return false
	}
	x.Used = append(x.Used, uint32(i))
	x.Keys[i] = value
	x.IDs[i] = int32(id)
	return true
}
func (x *Index) clear() {
	for _, i := range x.Used {
		x.Keys[i] = 0
	}
	x.Used = x.Used[:0]
}

type Dictionary struct {
	Limit  int64
	Index  Index
	E      []Expr
	Sorted [3][]Pair
	N0, N1 int
}

func newDictionary(limit int64) *Dictionary { return &Dictionary{Limit: limit, Index: newIndex()} }
func (d *Dictionary) add(value int64, a, b int, op byte, cost int) {
	if value > 0 && value <= d.Limit && d.Index.insert(value, len(d.E)) {
		d.E = append(d.E, Expr{value, int32(a), int32(b), op, int32(cost)})
	}
}
func (d *Dictionary) combine(i, j, cost int) {
	a, b := d.E[i].V, d.E[j].V
	d.add(a+b, i, j, '+', cost)
	if a != b {
		if a > b {
			d.add(a-b, i, j, '-', cost)
		} else {
			d.add(b-a, j, i, '-', cost)
		}
	}
	if a <= d.Limit/b {
		d.add(a*b, i, j, '*', cost)
	}
	if a > b && a%b == 0 {
		d.add(a/b, i, j, '/', cost)
	}
	if b > a && b%a == 0 {
		d.add(b/a, j, i, '/', cost)
	}
}
func (d *Dictionary) build(leaves []int64) {
	d.Index.clear()
	d.E = d.E[:0]
	for i := range d.Sorted {
		d.Sorted[i] = d.Sorted[i][:0]
	}
	for _, v := range leaves {
		d.add(v, -1, -1, 0, 0)
	}
	d.N0 = len(d.E)
	for i := 0; i < d.N0; i++ {
		for j := 0; j <= i; j++ {
			d.combine(i, j, 1)
		}
	}
	d.N1 = len(d.E)
	for i := d.N0; i < d.N1; i++ {
		for j := 0; j < d.N0; j++ {
			d.combine(i, j, 2)
		}
	}
	for i, e := range d.E {
		d.Sorted[e.Cost] = append(d.Sorted[e.Cost], Pair{e.V, int32(i)})
	}
	for i := range d.Sorted {
		arr := d.Sorted[i]
		sort.Slice(arr, func(a, b int) bool {
			if arr[a].V != arr[b].V {
				return arr[a].V < arr[b].V
			}
			return arr[a].ID < arr[b].ID
		})
	}
}

type Link struct {
	Factor, Atom int32
	Op           byte
}
type State struct {
	V            int64
	Cost, Parent int32
	Link         Link
	Rank         float64
}
type Search struct {
	D                          *Dictionary
	Width, Best, End, Terminal int
	History, Next              []State
	Visited                    map[int64]int
}

func newSearch(d *Dictionary, width int) *Search {
	return &Search{D: d, Width: width, Best: 1000, End: -1, Terminal: -1, Visited: make(map[int64]int)}
}
func (s *Search) exprCost(id int) int {
	if id < 0 {
		return 0
	}
	return 1 + int(s.D.E[id].Cost)
}
func (s *Search) offer(parent int, value int64, factor, atom int, op byte) {
	if value <= 0 || value > s.D.Limit {
		return
	}
	cost := int(s.History[parent].Cost) + s.exprCost(factor) + s.exprCost(atom)
	if cost >= s.Best {
		return
	}
	terminal := s.D.Index.get(value)
	if terminal >= 0 {
		if cost+int(s.D.E[terminal].Cost) < s.Best {
			s.Best = cost + int(s.D.E[terminal].Cost)
			s.End = len(s.History)
			s.Terminal = terminal
			s.History = append(s.History, State{V: value, Cost: int32(cost), Parent: int32(parent), Link: Link{int32(factor), int32(atom), op}})
		}
		return
	}
	if cost+3 >= s.Best || value >= s.History[parent].V {
		return
	}
	rank := float64(cost) + alpha*math.Log2(float64(value))
	if len(s.Next) == s.Width && rank >= s.Next[len(s.Next)-1].Rank {
		return
	}
	if prior, ok := s.Visited[value]; ok && prior <= cost {
		return
	}
	for i, current := range s.Next {
		if current.V == value {
			if int(current.Cost) <= cost {
				return
			}
			copy(s.Next[i:], s.Next[i+1:])
			s.Next = s.Next[:len(s.Next)-1]
			break
		}
	}
	state := State{value, int32(cost), int32(parent), Link{int32(factor), int32(atom), op}, rank}
	position := sort.Search(len(s.Next), func(i int) bool { return rank < s.Next[i].Rank })
	s.Next = append(s.Next, State{})
	copy(s.Next[position+1:], s.Next[position:])
	s.Next[position] = state
	if len(s.Next) > s.Width {
		s.Next = s.Next[:s.Width]
	}
}
func (s *Search) shifted(parent int, difference int64, atom int, op byte) {
	k := bits.TrailingZeros64(uint64(difference))
	for k >= 4 {
		factor := s.D.Index.get(int64(1) << k)
		if factor >= 0 {
			s.offer(parent, difference>>k, factor, atom, op)
			break
		}
		k--
	}
}
func (s *Search) expand(parent int) {
	v := s.History[parent].V
	d := s.D
	for _, arr := range d.Sorted {
		it := sort.Search(len(arr), func(i int) bool { return arr[i].V >= v })
		for k := -4; k < 4; k++ {
			position := it + k
			if position < 0 || position >= len(arr) {
				continue
			}
			a, id := arr[position].V, int(arr[position].ID)
			op := byte('r')
			if v > a {
				op = '+'
			}
			s.offer(parent, abs(v-a), -1, id, op)
		}
	}
	for i := 0; i < d.N1; i++ {
		a := d.E[i].V
		if a == 1 {
			continue
		}
		difference := abs(v - a)
		if difference != 0 {
			op := byte('r')
			if v > a {
				op = '+'
			}
			if d.Index.get(difference) >= 0 {
				s.offer(parent, difference, -1, i, op)
			}
			if v > a && (difference&15) == 0 {
				s.shifted(parent, difference, i, '+')
			}
			if a > v && (difference&15) == 0 {
				s.shifted(parent, difference, i, 'r')
			}
		}
		if a+v <= d.Limit {
			if d.Index.get(a+v) >= 0 {
				s.offer(parent, a+v, -1, i, '-')
			}
			if ((a + v) & 15) == 0 {
				s.shifted(parent, a+v, i, '-')
			}
		}
		if a >= v {
			continue
		}
		q, r := v/a, v%a
		if r == 0 {
			s.offer(parent, q, i, -1, '+')
			continue
		}
		rid := d.Index.get(r)
		if rid >= 0 {
			s.offer(parent, q, i, rid, '+')
		}
		if a-r <= d.Limit-v {
			rid = d.Index.get(a - r)
			if rid >= 0 {
				s.offer(parent, q+1, i, rid, '-')
			}
		}
		if i < d.N0 && q > 64 {
			for _, arr := range d.Sorted {
				it := sort.Search(len(arr), func(i int) bool { return arr[i].V >= q })
				for k := -1; k <= 0; k++ {
					position := it + k
					if position < 0 || position >= len(arr) {
						continue
					}
					b := arr[position].V
					if b > d.Limit/a {
						continue
					}
					product := a * b
					if product == v {
						continue
					}
					id := d.Index.get(product)
					if id >= 0 {
						op := byte('r')
						if product < v {
							op = '+'
						}
						s.offer(parent, abs(product-v), -1, id, op)
					}
				}
			}
		}
	}
}
func (s *Search) run(target int64) {
	s.History = append(s.History, State{V: target, Parent: -1, Link: Link{-1, -1, '+'}})
	if id := s.D.Index.get(target); id >= 0 {
		s.End = 0
		s.Terminal = id
		s.Best = int(s.D.E[id].Cost)
		return
	}
	front := []int{0}
	for depth := 0; depth < 20 && len(front) > 0; depth++ {
		s.Next = s.Next[:0]
		for _, parent := range front {
			if int(s.History[parent].Cost)+3 < s.Best {
				s.expand(parent)
			}
		}
		front = front[:0]
		for _, state := range s.Next {
			if int(state.Cost)+3 >= s.Best {
				continue
			}
			s.Visited[state.V] = int(state.Cost)
			front = append(front, len(s.History))
			s.History = append(s.History, state)
		}
	}
}

type Puzzle struct {
	Limit                  int64
	Starts, Targets, Order []int64
	Defs                   map[int64]Expr
	Args                   map[int64]Operands
	Width                  int
}

func newPuzzle(limit int64, width int) *Puzzle {
	return &Puzzle{Limit: limit, Width: width, Defs: make(map[int64]Expr), Args: make(map[int64]Operands)}
}
func (p *Puzzle) clone() *Puzzle {
	q := *p
	q.Defs = make(map[int64]Expr, len(p.Defs))
	for k, v := range p.Defs {
		q.Defs[k] = v
	}
	q.Args = make(map[int64]Operands, len(p.Args))
	for k, v := range p.Args {
		q.Args[k] = v
	}
	q.Order = append([]int64(nil), p.Order...)
	return &q
}
func (p *Puzzle) make(op byte, a, b int64) int64 {
	v := apply(op, a, b)
	if v <= 0 || v > p.Limit {
		panic("invalid construction")
	}
	if _, ok := p.Defs[v]; !ok {
		p.Defs[v] = Expr{V: v, A: -1, B: -1, Op: op}
		p.Args[v] = Operands{a, b}
		p.Order = append(p.Order, v)
	}
	return v
}
func (p *Puzzle) materialize(d *Dictionary, id int) int64 {
	e := d.E[id]
	if _, ok := p.Defs[e.V]; ok {
		return e.V
	}
	right := p.materialize(d, int(e.B))
	left := p.materialize(d, int(e.A))
	return p.make(e.Op, left, right)
}
func (p *Puzzle) reconstruct(d *Dictionary, s *Search, target int64) {
	v := p.materialize(d, s.Terminal)
	for at := s.End; s.History[at].Parent >= 0; at = int(s.History[at].Parent) {
		link := s.History[at].Link
		if link.Factor >= 0 {
			v = p.make('*', v, p.materialize(d, int(link.Factor)))
		}
		if link.Atom >= 0 {
			a := p.materialize(d, int(link.Atom))
			if link.Op == 'r' {
				v = p.make('-', a, v)
			} else {
				v = p.make(link.Op, v, a)
			}
		}
	}
	if v != target {
		panic("replanned search reconstruction failed")
	}
}
func (p *Puzzle) loadHint(hint []Instruction) {
	if len(p.Starts) == 0 || len(p.Starts) > 48 || len(hint) > 4096 {
		panic("invalid hint dimensions")
	}
	p.Defs = make(map[int64]Expr)
	p.Args = make(map[int64]Operands)
	p.Order = nil
	var bank [48]int64
	covered := make(IntSet)
	for i, v := range p.Starts {
		if v <= 0 || v > p.Limit {
			panic("invalid initial hint value")
		}
		bank[i] = v
		p.Defs[v] = Expr{V: v, A: -1, B: -1}
		covered[v] = true
	}
	for _, instruction := range hint {
		a, b, d := int(instruction.A), int(instruction.B), int(instruction.D)
		if a < 0 || a >= 48 || b < 0 || b >= 48 || d < 0 || d >= 48 {
			panic("invalid hint register")
		}
		first, second := bank[a], bank[b]
		if first == 0 || second == 0 {
			panic("undefined hint source")
		}
		op := instruction.Op
		if op != '+' && op != '-' && op != '*' && op != '/' {
			panic("invalid hint operator")
		}
		if (op == '-' && first <= second) || (op == '/' && first%second != 0) || (op == '*' && first > p.Limit/second) {
			panic("illegal hint arithmetic")
		}
		value := p.make(op, first, second)
		bank[d] = value
		covered[value] = true
	}
	for _, target := range p.Targets {
		if !covered[target] {
			panic("hint does not cover every target")
		}
	}
}
func (p *Puzzle) descendants(target int64) IntSet {
	users := make(map[int64][]int64)
	for _, value := range keys(p.Args) {
		operands := p.Args[value]
		users[operands.A] = append(users[operands.A], value)
		if operands.A != operands.B {
			users[operands.B] = append(users[operands.B], value)
		}
	}
	blocked := IntSet{target: true}
	queue := []int64{target}
	for i := 0; i < len(queue); i++ {
		for _, user := range users[queue[i]] {
			if !blocked[user] {
				blocked[user] = true
				queue = append(queue, user)
			}
		}
	}
	return blocked
}
func (p *Puzzle) compile() []Instruction {
	placed := make(IntSet)
	for _, v := range p.Starts {
		placed[v] = true
	}
	sequence := make([]int64, 0, len(p.Args))
	var visit func(int64)
	visit = func(value int64) {
		if placed[value] {
			return
		}
		placed[value] = true
		operands, ok := p.Args[value]
		if !ok {
			panic("missing compile recipe")
		}
		visit(operands.A)
		visit(operands.B)
		sequence = append(sequence, value)
	}
	for _, target := range p.Targets {
		visit(target)
	}
	uses := make(map[int64][]int, len(sequence))
	for i := len(sequence) - 1; i >= 0; i-- {
		operands := p.Args[sequence[i]]
		uses[operands.A] = append(uses[operands.A], i)
		if operands.A != operands.B {
			uses[operands.B] = append(uses[operands.B], i)
		}
	}
	var bank [48]int64
	copy(bank[:], p.Starts)
	seeds := make(IntSet)
	for _, v := range p.Starts {
		seeds[v] = true
	}
	program := make([]Instruction, 0, len(sequence))
	var emit func(int64, uint64) int
	emit = func(value int64, locked uint64) int {
		for i, v := range bank {
			if v == value {
				return i
			}
		}
		operands, ok := p.Args[value]
		if !ok {
			panic("missing emit recipe")
		}
		ra := emit(operands.A, locked)
		rb := emit(operands.B, locked|(uint64(1)<<ra))
		rd, latest := -1, -1
		for k, old := range bank {
			if (locked>>k)&1 != 0 || seeds[old] {
				continue
			}
			nextUse := 1000000
			if u := uses[old]; len(u) > 0 {
				nextUse = u[len(u)-1]
			}
			if old == 0 {
				rd = k
				break
			}
			if nextUse > latest {
				rd = k
				latest = nextUse
			}
		}
		if rd < 0 {
			panic("register allocation failed")
		}
		bank[rd] = value
		program = append(program, Instruction{p.Defs[value].Op, int32(ra), int32(rb), int32(rd)})
		return rd
	}
	for _, value := range sequence {
		operands := p.Args[value]
		uses[operands.A] = uses[operands.A][:len(uses[operands.A])-1]
		if operands.A != operands.B {
			uses[operands.B] = uses[operands.B][:len(uses[operands.B])-1]
		}
		emit(value, 0)
	}
	return program
}
func safeCompile(p *Puzzle) (result []Instruction, ok bool) {
	defer func() {
		if recover() != nil {
			result = nil
			ok = false
		}
	}()
	return p.compile(), true
}

type MT19937 struct {
	State [312]uint64
	At    int
}

func newMT(seed uint64) *MT19937 {
	r := &MT19937{At: 312}
	r.State[0] = seed
	for i := 1; i < 312; i++ {
		previous := r.State[i-1]
		r.State[i] = 6364136223846793005*(previous^(previous>>62)) + uint64(i)
	}
	return r
}
func (r *MT19937) next() uint64 {
	if r.At >= 312 {
		for i := 0; i < 312; i++ {
			x := (r.State[i] & 0xffffffff80000000) | (r.State[(i+1)%312] & 0x7fffffff)
			v := x >> 1
			if x&1 != 0 {
				v ^= 0xb5026f5aa96619e9
			}
			r.State[i] = r.State[(i+156)%312] ^ v
		}
		r.At = 0
	}
	y := r.State[r.At]
	r.At++
	y ^= (y >> 29) & 0x5555555555555555
	y ^= (y << 17) & 0x71d67fffeda60000
	y ^= (y << 37) & 0xfff7eee000000000
	y ^= y >> 43
	return y
}
func (r *MT19937) bounded(span uint64) uint64 {
	if span == 1 {
		return 0
	}
	w := bits.Len64(span - 1)
	mask := uint64(1)<<w - 1
	for {
		value := r.next() & mask
		if value < span {
			return value
		}
	}
}

type Ranked struct {
	Priority int
	V        int64
}

func shuffle(values []Ranked, rng *MT19937) {
	for i := 0; i+1 < len(values); i++ {
		offset := int(rng.bounded(uint64(len(values) - i)))
		if offset != 0 {
			values[i], values[i+offset] = values[i+offset], values[i]
		}
	}
}

func (p *Puzzle) polishHint(hint []Instruction) []Instruction {
	p.loadHint(hint)
	best := hint
	if normalized, ok := safeCompile(p); ok && len(normalized) < len(best) {
		best = normalized
	}
	d := newDictionary(p.Limit)
	rng := newMT(orderSeed)
	for pass := 0; pass < repairPasses; pass++ {
		targetUses := make(map[int64]int)
		fanout := make(map[int64]int)
		ancestors := make(map[int64]IntSet)
		for _, value := range keys(p.Args) {
			args := p.Args[value]
			fanout[args.A]++
			fanout[args.B]++
		}
		for _, target := range p.Targets {
			set, ok := ancestors[target]
			if !ok {
				set = make(IntSet)
				ancestors[target] = set
			}
			var visit func(int64)
			visit = func(value int64) {
				if set[value] {
					return
				}
				set[value] = true
				targetUses[value]++
				if a, ok := p.Args[value]; ok {
					visit(a.A)
					visit(a.B)
				}
			}
			visit(target)
		}
		ranked := make([]Ranked, 0, len(p.Targets))
		for _, target := range p.Targets {
			if _, ok := p.Args[target]; !ok {
				continue
			}
			exclusive := 0
			for value := range ancestors[target] {
				if _, ok := p.Args[value]; ok && targetUses[value] == 1 {
					exclusive++
				}
			}
			ranked = append(ranked, Ranked{-exclusive, target})
		}
		sort.Slice(ranked, func(i, j int) bool {
			if ranked[i].Priority != ranked[j].Priority {
				return ranked[i].Priority < ranked[j].Priority
			}
			return ranked[i].V < ranked[j].V
		})
		if orderSeed%3 == 1 {
			shuffle(ranked, rng)
		} else if orderSeed%3 == 2 {
			sort.SliceStable(ranked, func(i, j int) bool { return len(ancestors[ranked[i].V]) > len(ancestors[ranked[j].V]) })
		}
		attempts := len(ranked)
		if attempts > repairTrials {
			attempts = repairTrials
		}
		improved := false
		for attempt := 0; attempt < attempts; attempt++ {
			target := ranked[attempt].V
			blocked := p.descendants(target)
			leaves := make([]int64, 0, nRecent)
			used := make(IntSet)
			add := func(value int64) {
				if _, ok := p.Defs[value]; ok && !blocked[value] && !used[value] {
					used[value] = true
					leaves = append(leaves, value)
				}
			}
			for _, value := range p.Starts {
				add(value)
			}
			add(1)
			for _, exponent := range []uint{1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 16, 20, 24, 32, 40, 48} {
				add(int64(1) << exponent)
			}
			for _, value := range []int64{3, 5, 7, 9, 11, 13, 15, 17, 19, 23, 31} {
				add(value)
			}
			for i := len(p.Targets) - 1; i >= 0 && len(leaves) < nDone; i-- {
				add(p.Targets[i])
			}
			intermediates := make([]Ranked, 0, len(p.Defs))
			for _, value := range keys(p.Defs) {
				if !blocked[value] && !used[value] {
					intermediates = append(intermediates, Ranked{-fanout[value], value})
				}
			}
			sort.Slice(intermediates, func(i, j int) bool {
				if intermediates[i].Priority != intermediates[j].Priority {
					return intermediates[i].Priority < intermediates[j].Priority
				}
				return intermediates[i].V < intermediates[j].V
			})
			if orderSeed%2 == 1 {
				for i, j := 0, len(intermediates)-1; i < j; i, j = i+1, j-1 {
					intermediates[i], intermediates[j] = intermediates[j], intermediates[i]
				}
			}
			for _, item := range intermediates {
				if len(leaves) >= nRecent {
					break
				}
				add(item.V)
			}
			d.build(leaves)
			search := newSearch(d, p.Width)
			search.run(target)
			if search.End < 0 {
				continue
			}
			trial := p.clone()
			removedDefs := make(map[int64]Expr)
			removedArgs := make(map[int64]Operands)
			for _, value := range keys(blocked) {
				expression, ok := trial.Defs[value]
				if !ok {
					panic("missing blocked definition")
				}
				removedDefs[value] = expression
				if operands, ok := trial.Args[value]; ok {
					removedArgs[value] = operands
				}
				delete(trial.Defs, value)
				delete(trial.Args, value)
			}
			trial.reconstruct(d, search, target)
			for _, value := range keys(removedDefs) {
				if _, ok := trial.Defs[value]; !ok {
					trial.Defs[value] = removedDefs[value]
					if args, ok := removedArgs[value]; ok {
						trial.Args[value] = args
					}
				}
			}
			if candidate, ok := safeCompile(trial); ok && len(candidate) < len(best) {
				best = candidate
				*p = *trial
				improved = true
			}
		}
		if !improved {
			break
		}
	}
	return best
}

type Scanner struct {
	R      io.Reader
	Buffer [65536]byte
	At, N  int
	Err    error
}

func (s *Scanner) byte() (byte, bool) {
	if s.At == s.N {
		if s.Err != nil {
			return 0, false
		}
		s.N, s.Err = s.R.Read(s.Buffer[:])
		s.At = 0
		if s.N == 0 {
			return 0, false
		}
	}
	b := s.Buffer[s.At]
	s.At++
	return b, true
}
func (s *Scanner) first() (byte, bool) {
	for {
		b, ok := s.byte()
		if !ok {
			return 0, false
		}
		if b > ' ' {
			return b, true
		}
	}
}
func (s *Scanner) token() (string, bool) {
	b, ok := s.first()
	if !ok {
		return "", false
	}
	out := []byte{b}
	for {
		b, ok = s.byte()
		if !ok || b <= ' ' {
			return string(out), true
		}
		out = append(out, b)
	}
}
func (s *Scanner) integer() int64 {
	b, ok := s.first()
	if !ok {
		panic("truncated numeric input")
	}
	negative := false
	if b == '-' {
		negative = true
		b, ok = s.byte()
		if !ok {
			panic("truncated numeric input")
		}
	}
	value := int64(0)
	for {
		if b < '0' || b > '9' {
			panic("invalid numeric input")
		}
		value = value*10 + int64(b-'0')
		b, ok = s.byte()
		if !ok || b <= ' ' {
			break
		}
	}
	if negative {
		return -value
	}
	return value
}

func execute() {
	args := os.Args
	width := 3
	mode := 1
	if len(args) > 1 {
		width = mustInt(args[1])
	}
	if len(args) > 2 {
		value, err := strconv.ParseFloat(args[2], 64)
		if err != nil {
			panic(err)
		}
		alpha = value
	}
	if len(args) > 3 {
		nDone = mustInt(args[3])
	}
	if len(args) > 4 {
		nRecent = mustInt(args[4])
	}
	if len(args) > 5 {
		indexBits = mustInt(args[5])
	}
	if len(args) > 6 {
		mode = mustInt(args[6])
	}
	if len(args) > 7 {
		value, err := strconv.ParseUint(args[7], 10, 64)
		if err != nil {
			panic(err)
		}
		orderSeed = value
	}
	if len(args) > 8 {
		repairTrials = mustInt(args[8])
	}
	if len(args) > 9 {
		repairPasses = mustInt(args[9])
	}
	if mode != 6 {
		panic("the Go comparison port implements mode 6 only")
	}
	if repairTrials < 0 || repairTrials > 4096 || repairPasses < 1 || repairPasses > 100 {
		panic("invalid repair budget")
	}
	if indexBits < 1 || indexBits > 26 || width < 1 {
		panic("invalid dictionary configuration")
	}
	scanner := Scanner{R: os.Stdin}
	writer := bufio.NewWriterSize(os.Stdout, 65536)
	defer writer.Flush()
	for {
		id, ok := scanner.token()
		if !ok {
			break
		}
		limit := scanner.integer()
		ns, nt := int(scanner.integer()), int(scanner.integer())
		if limit < 1 || limit > 1000000000000000000 || ns < 1 || ns > 48 || nt < 0 || nt > 4096 {
			panic("invalid puzzle dimensions")
		}
		p := newPuzzle(limit, width)
		p.Starts = make([]int64, ns)
		p.Targets = make([]int64, nt)
		for i := range p.Starts {
			p.Starts[i] = scanner.integer()
		}
		for i := range p.Targets {
			p.Targets[i] = scanner.integer()
		}
		count := int(scanner.integer())
		if count < 0 || count > 4096 {
			panic("invalid hint instruction count")
		}
		hint := make([]Instruction, count)
		for i := range hint {
			op, ok := scanner.token()
			if !ok || len(op) != 1 {
				panic("truncated hint program")
			}
			hint[i] = Instruction{op[0], int32(scanner.integer()), int32(scanner.integer()), int32(scanner.integer())}
		}
		program := p.polishHint(hint)
		out := make([]byte, 0, len(program)*20+len(id)+4)
		out = append(out, id...)
		out = append(out, ' ', '[')
		for i, instruction := range program {
			if i > 0 {
				out = append(out, ',')
			}
			out = append(out, '[', '"', instruction.Op, '"', ',')
			out = strconv.AppendInt(out, int64(instruction.A), 10)
			out = append(out, ',')
			out = strconv.AppendInt(out, int64(instruction.B), 10)
			out = append(out, ',')
			out = strconv.AppendInt(out, int64(instruction.D), 10)
			out = append(out, ']')
		}
		out = append(out, ']', '\n')
		if _, err := writer.Write(out); err != nil {
			panic(err)
		}
		if err := writer.Flush(); err != nil {
			panic(err)
		}
	}
}
func mustInt(value string) int {
	n, err := strconv.Atoi(value)
	if err != nil {
		panic(err)
	}
	return n
}
func main() {
	defer func() {
		if err := recover(); err != nil {
			fmt.Fprintln(os.Stderr, "Solver error:", err)
			os.Exit(1)
		}
	}()
	execute()
}
