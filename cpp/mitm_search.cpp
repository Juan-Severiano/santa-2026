// SPDX-License-Identifier: Apache-2.0
// Exact short-recipe meet-in-the-middle repair, developed for this repository.
// Reuses the credited Tony Li / konbu17 value graph and register allocator.
// See beam_search.cpp, THIRD_PARTY.md and licenses/Apache-2.0.txt.
//
// Input: id limit ns nt, starts, targets, n_hint, then op i j d instructions.
// Args: leaves=48 repairs=8 seed=0 context=3 index_bits=22 candidates=24 passes=1
// The context argument is retained for reproducibility; exact exclusive cuts
// supersede the old bounded-ancestor heuristic.

#define main santa2026_embedded_beam_main
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wreturn-type"
#include "beam_search.cpp"
#pragma GCC diagnostic pop
#undef main

#include <chrono>
#include <tuple>

namespace mitm {

struct Candidate {
    int a = -1, b = -1;
    char op = 0;
    int cost = 0;
};

struct Cut {
    set<Int> outside;
    set<Int> blocked;
    int removed = 0;
};

Cut exclusive_cut(const Puzzle &puzzle, Int hub) {
    Cut cut;
    auto outside = [&](auto &&self, Int value) -> void {
        if (value == hub || !cut.outside.insert(value).second)
            return;
        auto recipe = puzzle.args.find(value);
        if (recipe != puzzle.args.end()) {
            self(self, recipe->second.first);
            self(self, recipe->second.second);
        }
    };
    for (Int target : puzzle.targets)
        outside(outside, target);
    for (Int initial : puzzle.starts)
        cut.outside.insert(initial);
    set<Int> ancestry;
    auto visit = [&](auto &&self, Int value) -> void {
        if (!ancestry.insert(value).second)
            return;
        auto recipe = puzzle.args.find(value);
        if (recipe != puzzle.args.end()) {
            self(self, recipe->second.first);
            self(self, recipe->second.second);
        }
    };
    visit(visit, hub);
    cut.blocked = puzzle.descendants(hub);
    for (Int value : ancestry)
        if (puzzle.args.count(value) && !cut.outside.count(value)) {
            ++cut.removed;
            auto descendants = puzzle.descendants(value);
            cut.blocked.insert(descendants.begin(), descendants.end());
        }
    return cut;
}

vector<Int> choose_leaves(const Puzzle &puzzle, const Cut &cut, int budget, uint64_t seed) {
    vector<Int> leaves;
    set<Int> chosen;
    auto add = [&](Int value) {
        if (int(leaves.size()) < budget && puzzle.defs.count(value) &&
            !cut.blocked.count(value) && cut.outside.count(value) && chosen.insert(value).second)
            leaves.push_back(value);
    };
    for (Int value : puzzle.starts)
        add(value);
    add(1);
    // The values are actual graph nodes, never implicitly free new constants.
    for (int exponent : {1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 16, 20, 24, 32, 40, 48})
        add(Int(1) << exponent);
    map<Int, int> fanout;
    for (auto &[value, recipe] : puzzle.args)
        if (cut.outside.count(value)) {
            ++fanout[recipe.first];
            if (recipe.first != recipe.second)
                ++fanout[recipe.second];
        }
    vector<pair<int, Int>> remaining;
    for (Int value : cut.outside)
        if (!cut.blocked.count(value) && !chosen.count(value) && puzzle.defs.count(value))
            remaining.push_back({-fanout[value], value});
    sort(remaining.begin(), remaining.end());
    if (seed % 3 == 1) {
        mt19937_64 generator(seed);
        shuffle(remaining.begin(), remaining.end(), generator);
    } else if (seed % 3 == 2) {
        stable_sort(remaining.begin(), remaining.end(), [](auto a, auto b) {
            return make_pair(a.first, -a.second) < make_pair(b.first, -b.second);
        });
    }
    for (auto [priority, value] : remaining)
        add(value);
    return leaves;
}

void build_dictionary(Dictionary &dictionary, const vector<Int> &leaves) {
    dictionary.build(leaves);
    // x+x and x*x cost two instructions when the one-op x is shared. The
    // inherited tree enumeration otherwise combines one-op values with leaves.
    size_t previous = dictionary.e.size();
    for (int index = dictionary.n0; index < dictionary.n1; ++index)
        dictionary.combine(index, index, 2);
    for (size_t index = previous; index < dictionary.e.size(); ++index)
        dictionary.sorted[2].push_back({dictionary.e[index].v, int(index)});
    sort(dictionary.sorted[2].begin(), dictionary.sorted[2].end());
}

int shared_cost(const Dictionary &dictionary, int a, int b, bool final_operation) {
    // There are at most four non-leaf nodes in two depth-two recipes.
    array<int, 8> nodes{};
    int count = 0;
    auto collect = [&](auto &&self, int index) -> void {
        if (index < 0 || dictionary.e[index].cost == 0)
            return;
        for (int i = 0; i < count; ++i)
            if (nodes[i] == index)
                return;
        if (count >= int(nodes.size()))
            throw runtime_error("unexpected MITM recipe depth");
        nodes[count++] = index;
        const Expr &expression = dictionary.e[index];
        self(self, expression.a);
        self(self, expression.b);
    };
    collect(collect, a);
    collect(collect, b);
    return count + int(final_operation);
}

vector<Candidate> search(Dictionary &dictionary, Int target, int max_candidates) {
    vector<Candidate> candidates;
    set<tuple<char, int, int>> seen;
    auto offer = [&](char operation, int a, int b) {
        if (a < 0 || (operation && b < 0))
            return;
        if ((operation == '+' || operation == '*') && a > b)
            swap(a, b);
        if (!seen.insert({operation, a, b}).second)
            return;
        int cost = shared_cost(dictionary, a, b, operation != 0);
        if (cost > 5)
            return;
        Candidate candidate{a, b, operation, cost};
        auto key = [](const Candidate &item) {
            return make_tuple(item.cost, item.op, item.a, item.b);
        };
        auto position = lower_bound(candidates.begin(), candidates.end(), key(candidate),
                                    [&](const Candidate &item, auto value) { return key(item) < value; });
        candidates.insert(position, candidate);
        if (int(candidates.size()) > max_candidates)
            candidates.pop_back();
    };
    int direct = dictionary.index.get(target);
    if (direct >= 0)
        offer(0, direct, -1);

    // Splits (0, <=2), (1, <=2); each inverse lookup is exact.
    for (int index = 0; index < dictionary.n1; ++index) {
        Int value = dictionary.e[index].v;
        if (value < target)
            offer('+', index, dictionary.index.get(target - value));
        if (value > target)
            offer('-', index, dictionary.index.get(value - target));
        if (value <= dictionary.limit - target)
            offer('-', dictionary.index.get(target + value), index);
        if (target % value == 0)
            offer('*', index, dictionary.index.get(target / value));
        if (value % target == 0)
            offer('/', index, dictionary.index.get(value / target));
        if (value <= dictionary.limit / target)
            offer('/', dictionary.index.get(target * value), index);
    }

    const auto &two = dictionary.sorted[2];
    // Split (2,2): sorted two-pointer joins for addition and subtraction.
    if (!two.empty()) {
        size_t first = 0, last = two.size() - 1;
        while (first <= last) {
            Int sum = two[first].first + two[last].first;
            if (sum == target) {
                offer('+', two[first].second, two[last].second);
                ++first;
                if (last == 0)
                    break;
                --last;
            } else if (sum < target)
                ++first;
            else {
                if (last == 0)
                    break;
                --last;
            }
        }
        size_t low = 0, high = 0;
        while (high < two.size() && low < two.size()) {
            if (low >= high)
                high = low + 1;
            if (high == two.size())
                break;
            Int difference = two[high].first - two[low].first;
            if (difference == target) {
                offer('-', two[high].second, two[low].second);
                ++high;
                ++low;
            } else if (difference < target)
                ++high;
            else
                ++low;
        }
    }
    // Multiplication joins use divisibility; exact division uses a bounded
    // product lookup, never an overflowing intermediate.
    for (auto [value, index] : two) {
        if (value <= target / value && target % value == 0) {
            int other = dictionary.index.get(target / value);
            if (other >= 0 && dictionary.e[other].cost == 2)
                offer('*', index, other);
        }
        if (value <= dictionary.limit / target) {
            int other = dictionary.index.get(target * value);
            if (other >= 0 && dictionary.e[other].cost == 2)
                offer('/', other, index);
        }
    }
    return candidates;
}

vector<array<int, 4>> repair(Puzzle puzzle, const vector<array<int, 4>> &hint,
                            int leaves, int trials, uint64_t seed, int max_candidates, int passes) {
    puzzle.load_hint(hint);
    vector<array<int, 4>> best = hint;
    try {
        auto normalized = puzzle.compile();
        if (normalized.size() < best.size())
            best = std::move(normalized);
    } catch (const exception &) {
    }
    Dictionary dictionary(puzzle.limit);
    for (int pass = 0; pass < passes; ++pass) {
        vector<tuple<int, int, Int>> hubs;
        map<Int, int> fanout;
        set<Int> active;
        auto reach = [&](auto &&self, Int value) -> void {
            if (!active.insert(value).second)
                return;
            auto recipe = puzzle.args.find(value);
            if (recipe != puzzle.args.end()) {
                self(self, recipe->second.first);
                self(self, recipe->second.second);
            }
        };
        for (Int target : puzzle.targets)
            reach(reach, target);
        for (auto &[value, operands] : puzzle.args) {
            if (!active.count(value))
                continue;
            ++fanout[operands.first];
            if (operands.first != operands.second)
                ++fanout[operands.second];
        }
        for (auto &[value, operands] : puzzle.args) {
            if (!active.count(value))
                continue;
            Cut cut = exclusive_cut(puzzle, value);
            if (cut.removed >= 3)
                hubs.push_back({-cut.removed, -fanout[value], value});
        }
        sort(hubs.begin(), hubs.end());
        if (seed % 4 == 3) {
            mt19937_64 generator(seed + pass);
            shuffle(hubs.begin(), hubs.end(), generator);
        }
        bool improved = false;
        int attempts = 0;
        for (auto [priority, usage, hub] : hubs) {
            if (attempts++ >= trials)
                break;
            Cut cut = exclusive_cut(puzzle, hub);
            vector<Int> free_values = choose_leaves(puzzle, cut, leaves, seed);
            if (free_values.empty())
                continue;
            build_dictionary(dictionary, free_values);
            auto candidates = search(dictionary, hub, max_candidates);
            for (const Candidate &candidate : candidates) {
                Puzzle trial = puzzle;
                map<Int, Expr> removed_definitions;
                map<Int, pair<Int, Int>> removed_recipes;
                for (Int value : cut.blocked) {
                    auto expression = trial.defs.find(value);
                    if (expression == trial.defs.end())
                        continue;
                    removed_definitions[value] = expression->second;
                    auto recipe = trial.args.find(value);
                    if (recipe != trial.args.end())
                        removed_recipes[value] = recipe->second;
                    trial.defs.erase(value);
                    trial.args.erase(value);
                }
                try {
                    Int left = trial.materialize(dictionary, candidate.a);
                    Int value = left;
                    if (candidate.op) {
                        Int right = trial.materialize(dictionary, candidate.b);
                        value = trial.make(candidate.op, left, right);
                    }
                    if (value != hub)
                        throw runtime_error("MITM reconstruction mismatch");
                    for (auto &[old_value, expression] : removed_definitions)
                        if (!trial.defs.count(old_value)) {
                            trial.defs[old_value] = expression;
                            auto recipe = removed_recipes.find(old_value);
                            if (recipe != removed_recipes.end())
                                trial.args[old_value] = recipe->second;
                        }
                    auto program = trial.compile();
                    if (program.size() < best.size()) {
                        best = std::move(program);
                        puzzle = std::move(trial);
                        improved = true;
                        break;
                    }
                } catch (const exception &) {
                    // Register pressure or unsupported seed overwrites must
                    // never discard the valid original hint.
                }
            }
        }
        if (!improved)
            break;
    }
    return best;
}

} // namespace mitm

int main(int argc, char **argv) try {
    int leaves = argc > 1 ? stoi(argv[1]) : 48;
    int trials = argc > 2 ? stoi(argv[2]) : 8;
    uint64_t seed = argc > 3 ? stoull(argv[3]) : 0;
    int context = argc > 4 ? stoi(argv[4]) : 3;
    INDEX_BITS = argc > 5 ? stoi(argv[5]) : 22;
    int candidates = argc > 6 ? stoi(argv[6]) : 24;
    int passes = argc > 7 ? stoi(argv[7]) : 1;
    if (leaves < 4 || leaves > 64 || trials < 0 || trials > 256 || context < 1 || context > 8 ||
        INDEX_BITS < 20 || INDEX_BITS > 25 || candidates < 1 || candidates > 128 || passes < 1 || passes > 10)
        throw runtime_error("invalid MITM budget");
    ORDER_MODE = 7; // Includes exact self-division when constructing a unit.
    string identifier;
    Int limit;
    int ns, nt;
    while (cin >> identifier >> limit >> ns >> nt) {
        if (limit < 1 || limit > 1000000000000000000LL || ns < 1 || ns > 48 || nt < 0 || nt > 4096)
            throw runtime_error("invalid MITM puzzle dimensions");
        Puzzle puzzle(limit, 1);
        puzzle.starts.resize(ns);
        puzzle.targets.resize(nt);
        for (Int &value : puzzle.starts)
            if (!(cin >> value))
                throw runtime_error("truncated starting values");
        for (Int &value : puzzle.targets)
            if (!(cin >> value))
                throw runtime_error("truncated targets");
        int count;
        if (!(cin >> count) || count < 0 || count > 4096)
            throw runtime_error("invalid MITM hint size");
        vector<array<int, 4>> hint;
        for (int index = 0; index < count; ++index) {
            char operation;
            int first, second, destination;
            if (!(cin >> operation >> first >> second >> destination))
                throw runtime_error("truncated MITM hint");
            hint.push_back({operation, first, second, destination});
        }
        auto result = mitm::repair(std::move(puzzle), hint, leaves, trials, seed, candidates, passes);
        cout << identifier << " [";
        for (size_t index = 0; index < result.size(); ++index) {
            if (index)
                cout << ',';
            auto [operation, first, second, destination] = result[index];
            cout << "[\"" << char(operation) << "\"," << first << ',' << second << ',' << destination << ']';
        }
        cout << "]\n" << flush;
    }
} catch (const exception &error) {
    cerr << "MITM error: " << error.what() << '\n';
    return 1;
}
