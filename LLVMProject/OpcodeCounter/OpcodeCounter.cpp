#include <map>
#include <string>

#include "llvm/IR/Function.h"
#include "llvm/IR/PassManager.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Passes/PassPlugin.h"
#include "llvm/Support/raw_ostream.h"

using namespace llvm;

namespace {

class OpcodeCounter : public PassInfoMixin<OpcodeCounter> {
public:
    PreservedAnalyses run(Function &F, FunctionAnalysisManager &) {
        std::map<std::string, unsigned> opcodeCounts;

        for (auto &BB : F)
            for (auto &I : BB)
                opcodeCounts[I.getOpcodeName()]++;

        outs() << "---------------------------------------------\n";
        outs() << "Opcode Counts for Function: " << F.getName() << "\n";
        for (auto &entry : opcodeCounts)
            outs() << entry.first << " : " << entry.second << "\n";
        outs() << "---------------------------------------------\n";

        return PreservedAnalyses::all();
    }
};

} // end anonymous namespace


extern "C" LLVM_ATTRIBUTE_WEAK ::llvm::PassPluginLibraryInfo llvmGetPassPluginInfo() {
    return {
        LLVM_PLUGIN_API_VERSION,
        "OpcodeCounter",
        "1.0",
        [](PassBuilder &PB) {

            PB.registerPipelineParsingCallback(
                [](StringRef Name, FunctionPassManager &FPM,
                   ArrayRef<PassBuilder::PipelineElement>) {
                    if (Name == "opcode-counter") {
                        FPM.addPass(OpcodeCounter());
                        return true;
                    }
                    return false;
                });
        }
    };
}
